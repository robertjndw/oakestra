import logging

from ext_requests.credentials_db import (
    mongo_get_credential_by_id,
    mongo_get_credential_by_name_and_org,
    mongo_get_credential_by_name_and_owner,
)
from ext_requests.organization_db import mongo_get_roles_of_user_in_organization

from credentials import registry
from credentials.crypto import decrypt_payload

logger = logging.getLogger("system_manager")


class CredentialError(Exception):
    pass


class CredentialNotFoundError(CredentialError):
    pass


class CredentialPermissionError(CredentialError):
    pass


def resolve_credential_ref(
    name: str, use_as: str, username: str, organization_id: str | None
) -> str:
    """
    Resolve a credential reference (name + use_as) to its credential_id.
    Enforces scope-based access and validates use_as against the handler.
    Returns the credential _id as a string.
    """
    record = mongo_get_credential_by_name_and_owner(name, username)
    if record is None and organization_id:
        record = mongo_get_credential_by_name_and_org(name, organization_id)
        if record is not None:
            roles = mongo_get_roles_of_user_in_organization(username, organization_id)
            if not roles:
                raise CredentialPermissionError(
                    f"Credential '{name}' is organization-scoped but user is not a member"
                )

    if record is None:
        raise CredentialNotFoundError(f"Credential '{name}' not found or not accessible")

    handler = registry.get_handler(record["type"])
    if handler is None:
        raise CredentialError(f"Unknown credential type '{record['type']}'")

    if use_as not in handler.valid_uses:
        raise CredentialError(
            f"Credential type '{record['type']}' does not support use_as='{use_as}'"
        )

    return str(record["_id"])


def materialize_credential(credential_id: str, use_as: str) -> dict:
    """
    Decrypt and materialize a credential for delivery to a worker.
    Returns a dict with use_as, type, and the plaintext value dict.
    """
    record = mongo_get_credential_by_id(credential_id)
    if record is None:
        raise CredentialNotFoundError(f"Credential {credential_id} not found")

    handler = registry.get_handler(record["type"])
    if handler is None:
        raise CredentialError(f"Unknown credential type '{record['type']}'")

    payload = decrypt_payload(record["data_ciphertext"])
    value = handler.materialize(payload, record.get("metadata", {}), use_as)

    return {
        "use_as": use_as,
        "type": record["type"],
        "value": value,
    }
