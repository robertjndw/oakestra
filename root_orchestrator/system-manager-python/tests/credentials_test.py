import os
import sys
from unittest.mock import MagicMock

import mongomock
import pymongo
import pytest
from cryptography.fernet import Fernet

# A stable key for the whole module so encrypt/decrypt round-trips are deterministic.
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# net_plugin_requests pulls in networking side effects we do not need here.
sys.modules["ext_requests.net_plugin_requests"] = MagicMock()

from blueprints.credentials_blueprints import (  # noqa: E402
    _check_read_access,
    _check_write_access,
    _is_admin,
)
from bson import ObjectId  # noqa: E402
from credentials import crypto, registry  # noqa: E402
from credentials.resolver import (  # noqa: E402
    CredentialError,
    CredentialNotFoundError,
    CredentialPermissionError,
    materialize_credential,
    resolve_credential_ref,
)
from credentials.types.docker_registry import DockerRegistryHandler  # noqa: E402
from ext_requests import credentials_db, mongodb_client  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_credential_globals():
    """Pin the module-global credential state across tests.

    Snapshots the Fernet cache and type registry, registers the built-in
    handlers once, and restores both afterward so tests stay order-independent
    instead of each having to null/re-register globals by hand.
    """
    saved_fernet = crypto._fernet
    saved_registry = dict(registry._registry)
    registry.register_builtin()
    yield
    crypto._fernet = saved_fernet
    registry._registry.clear()
    registry._registry.update(saved_registry)


def mockdb():
    client = pymongo.MongoClient("mongodb://localhost:10007/users")
    mongodb_client.mongo_credentials = client.db["credentials"]
    mongodb_client.mongo_organization = client.db["organization"]
    mongodb_client.mongo_users = client.db["user"]
    mongodb_client.app = MagicMock()


def _make_credential(name="reg", owner="alice", scope="private", organization_id=None):
    record = {
        "name": name,
        "type": "DockerRegistry",
        "scope": scope,
        "owner_user_id": owner,
        "metadata": {"username": "alice", "registry": "ghcr.io"},
        "data_ciphertext": crypto.encrypt_payload({"password": "s3cret"}),
    }
    if organization_id:
        record["organization_id"] = organization_id
    return credentials_db.mongo_create_credential(record)


def _seed_org(member_user="alice", roles=("Application_Provider",)):
    org_id = mongodb_client.mongo_organization.insert_one(
        {"name": "acme", "member": [{"user_id": member_user, "roles": list(roles)}]}
    ).inserted_id
    return str(org_id)


# --------------------------------------------------------------------------- #
# crypto
# --------------------------------------------------------------------------- #
def test_crypto_round_trip():
    payload = {"password": "hunter2", "nested": {"a": 1}}
    ciphertext = crypto.encrypt_payload(payload)
    assert isinstance(ciphertext, str)
    assert "hunter2" not in ciphertext  # encrypted, not plaintext
    assert crypto.decrypt_payload(ciphertext) == payload


def test_crypto_is_enabled_tracks_init():
    assert crypto.is_enabled() is False
    crypto.init_crypto()
    assert crypto.is_enabled() is True


def test_crypto_init_without_key_raises():
    saved = os.environ.pop("CREDENTIAL_ENCRYPTION_KEY")
    try:
        with pytest.raises(RuntimeError):
            crypto.init_crypto()
        assert crypto.is_enabled() is False
    finally:
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = saved


# --------------------------------------------------------------------------- #
# DockerRegistry type handler
# --------------------------------------------------------------------------- #
def test_handler_validate_rejects_missing_fields():
    h = DockerRegistryHandler()
    with pytest.raises(ValueError):
        h.validate({"password": ""}, {"username": "u"})
    with pytest.raises(ValueError):
        h.validate({"password": "p"}, {"username": ""})
    h.validate({"password": "p"}, {"username": "u"})  # valid, no raise


def test_handler_public_view_hides_secret_and_defaults_registry():
    h = DockerRegistryHandler()
    record = {
        "_id": ObjectId(),
        "name": "reg",
        "type": "DockerRegistry",
        "scope": "private",
        "metadata": {"username": "u"},  # no registry -> default
        "data_ciphertext": "irrelevant",
    }
    view = h.public_view(record)
    assert view["registry"] == DockerRegistryHandler.DEFAULT_REGISTRY
    assert "password" not in view
    assert "data_ciphertext" not in view


def test_handler_materialize_defaults_registry():
    h = DockerRegistryHandler()
    value = h.materialize({"password": "p"}, {"username": "u"}, "image_pull")
    assert value == {"username": "u", "password": "p", "registry": "docker.io"}


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_registry_register_builtin():
    registry.register_builtin()
    assert registry.get_handler("DockerRegistry") is not None
    assert registry.get_handler("Nonexistent") is None
    assert "DockerRegistry" in registry.get_registered_types()


# --------------------------------------------------------------------------- #
# credentials_db CRUD
# --------------------------------------------------------------------------- #
@mongomock.patch(servers=(("localhost", 10007),))
def test_db_create_get_update_delete():
    mockdb()
    cred_id = _make_credential()

    fetched = credentials_db.mongo_get_credential_by_id(cred_id)
    assert fetched is not None
    assert fetched["created_at"] == fetched["updated_at"]

    by_owner = credentials_db.mongo_get_credential_by_name_and_owner("reg", "alice")
    assert by_owner["_id"] == ObjectId(cred_id)

    updated = credentials_db.mongo_update_credential(cred_id, {"metadata": {"x": 1}})
    assert updated["metadata"] == {"x": 1}
    assert updated["updated_at"] >= updated["created_at"]

    assert credentials_db.mongo_delete_credential(cred_id) is True
    assert credentials_db.mongo_get_credential_by_id(cred_id) is None


@mongomock.patch(servers=(("localhost", 10007),))
def test_db_get_by_id_handles_bad_id():
    mockdb()
    assert credentials_db.mongo_get_credential_by_id("not-an-objectid") is None


@mongomock.patch(servers=(("localhost", 10007),))
def test_db_list_merges_private_and_org():
    mockdb()
    _make_credential(name="priv", owner="alice")
    _make_credential(name="org", owner="bob", scope="organization", organization_id="org1")

    only_private = credentials_db.mongo_list_credentials("alice", None)
    assert {c["name"] for c in only_private} == {"priv"}

    with_org = credentials_db.mongo_list_credentials("alice", "org1")
    assert {c["name"] for c in with_org} == {"priv", "org"}


# --------------------------------------------------------------------------- #
# resolver
# --------------------------------------------------------------------------- #
@mongomock.patch(servers=(("localhost", 10007),))
def test_resolve_private_credential():
    mockdb()
    cred_id = _make_credential(name="reg", owner="alice")
    assert resolve_credential_ref("reg", "image_pull", "alice", None) == cred_id


@mongomock.patch(servers=(("localhost", 10007),))
def test_resolve_org_credential_for_member():
    mockdb()
    org_id = _seed_org(member_user="alice")
    cred_id = _make_credential(
        name="shared", owner="creator", scope="organization", organization_id=org_id
    )
    assert resolve_credential_ref("shared", "image_pull", "alice", org_id) == cred_id


@mongomock.patch(servers=(("localhost", 10007),))
def test_resolve_org_credential_denied_for_non_member():
    mockdb()
    org_id = _seed_org(member_user="alice")
    _make_credential(name="shared", owner="creator", scope="organization", organization_id=org_id)
    with pytest.raises(CredentialPermissionError):
        resolve_credential_ref("shared", "image_pull", "bob", org_id)


@mongomock.patch(servers=(("localhost", 10007),))
def test_resolve_missing_credential_raises():
    mockdb()
    with pytest.raises(CredentialNotFoundError):
        resolve_credential_ref("ghost", "image_pull", "alice", None)


@mongomock.patch(servers=(("localhost", 10007),))
def test_resolve_invalid_use_as_raises():
    mockdb()
    _make_credential(name="reg", owner="alice")
    with pytest.raises(CredentialError):
        resolve_credential_ref("reg", "image_push", "alice", None)


@mongomock.patch(servers=(("localhost", 10007),))
def test_materialize_decrypts_value():
    mockdb()
    cred_id = _make_credential(name="reg", owner="alice")
    result = materialize_credential(cred_id, "image_pull")
    assert result["use_as"] == "image_pull"
    assert result["type"] == "DockerRegistry"
    assert result["value"]["password"] == "s3cret"
    assert result["value"]["registry"] == "ghcr.io"


@mongomock.patch(servers=(("localhost", 10007),))
def test_materialize_missing_raises():
    mockdb()
    with pytest.raises(CredentialNotFoundError):
        materialize_credential(str(ObjectId()), "image_pull")


# --------------------------------------------------------------------------- #
# blueprint access control
# --------------------------------------------------------------------------- #
def test_is_admin():
    assert _is_admin({"roles": ["Admin"]}) is True
    assert _is_admin({"roles": ["Application_Provider"]}) is False
    assert _is_admin({}) is False


@mongomock.patch(servers=(("localhost", 10007),))
def test_check_read_access_private():
    mockdb()
    record = {"scope": "private", "owner_user_id": "alice"}
    no_roles = {"roles": []}
    assert _check_read_access(record, "alice", None, no_roles) is True
    assert _check_read_access(record, "bob", None, no_roles) is False
    # admin bypasses ownership
    assert _check_read_access(record, "bob", None, {"roles": ["Admin"]}) is True


@mongomock.patch(servers=(("localhost", 10007),))
def test_check_read_access_organization():
    mockdb()
    org_id = _seed_org(member_user="alice")
    record = {"scope": "organization", "owner_user_id": "creator", "organization_id": org_id}
    no_roles = {"roles": []}
    assert _check_read_access(record, "alice", org_id, no_roles) is True
    assert _check_read_access(record, "bob", org_id, no_roles) is False
    # mismatched org is denied even for a member of a different org
    assert _check_read_access(record, "alice", "other_org", no_roles) is False


@mongomock.patch(servers=(("localhost", 10007),))
def test_check_write_access_requires_org_admin():
    mockdb()
    org_id = _seed_org(member_user="alice", roles=("Application_Provider",))
    admin_org_id = _seed_org(member_user="carol", roles=("Organization_Admin",))
    no_roles = {"roles": []}

    owner_record = {"scope": "private", "owner_user_id": "alice"}
    assert _check_write_access(owner_record, "alice", None, no_roles) is True

    org_record = {"scope": "organization", "owner_user_id": "creator", "organization_id": org_id}
    # plain member cannot write
    assert _check_write_access(org_record, "alice", org_id, no_roles) is False

    admin_record = {
        "scope": "organization",
        "owner_user_id": "creator",
        "organization_id": admin_org_id,
    }
    assert _check_write_access(admin_record, "carol", admin_org_id, no_roles) is True
