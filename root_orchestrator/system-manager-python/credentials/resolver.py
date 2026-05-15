import logging
import time

from ext_requests.credentials_db import (
    mongo_get_credential_by_id,
    mongo_get_credential_by_name_and_org,
    mongo_get_credential_by_name_and_owner,
)
from ext_requests.organization_db import mongo_get_roles_of_user_in_organization
from ext_requests.worker_keys_db import mongo_get_worker_key

from credentials import registry
from credentials.crypto import canonical_context_info, decrypt_payload, seal_for_worker

logger = logging.getLogger("system_manager")


class CredentialError(Exception):
    pass


class CredentialNotFoundError(CredentialError):
    pass


class CredentialPermissionError(CredentialError):
    pass


class WorkerKeyNotFoundError(CredentialError):
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
            # Ensure caller is a member of the organization
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


def seal_credential_for_worker(
    credential_id: str,
    use_as: str,
    worker_id: str,
    job_id: str,
    instance_number: int,
) -> dict:
    """
    Materialize and HPKE-seal a credential for a specific worker.
    Returns a dict suitable for the cluster_manager to include in the MQTT deploy payload.
    """
    record = mongo_get_credential_by_id(credential_id)
    if record is None:
        raise CredentialNotFoundError(f"Credential {credential_id} not found")

    handler = registry.get_handler(record["type"])
    if handler is None:
        raise CredentialError(f"Unknown credential type '{record['type']}'")

    worker_key = mongo_get_worker_key(worker_id)
    if worker_key is None:
        raise WorkerKeyNotFoundError(
            f"No public key registered for worker '{worker_id}'. "
            "Worker may not have completed registration yet."
        )

    payload = decrypt_payload(record["data_ciphertext"])
    materialized = handler.materialize(payload, record.get("metadata", {}), use_as)

    key_id = worker_key["key_id"]
    unix_ts = int(time.time())

    ctx = canonical_context_info(
        credential_id=credential_id,
        job_id=job_id,
        instance_number=instance_number,
        worker_id=worker_id,
        key_id=key_id,
        unix_ts=unix_ts,
    )

    ciphertext_b64 = seal_for_worker(
        pub_keyset_b64=worker_key["pub_keyset_b64"],
        materialized=materialized,
        context_info=ctx,
        key_id=key_id,
    )

    logger.info(
        f"Sealed credential {credential_id} for worker {worker_id} "
        f"job {job_id} instance {instance_number} key_id={key_id}"
    )

    return {
        "use_as": use_as,
        "type": record["type"],
        "credential_id": credential_id,
        "instance_number": instance_number,
        "ciphertext_b64": ciphertext_b64,
        "key_id": key_id,
        "unix_ts": unix_ts,
    }
