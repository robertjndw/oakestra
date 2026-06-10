import logging

from bson import json_util
from credentials import registry
from credentials.crypto import decrypt_payload, encrypt_payload
from credentials.crypto import is_enabled as _credentials_enabled
from ext_requests.credentials_db import (
    mongo_create_credential,
    mongo_delete_credential,
    mongo_get_credential_by_id,
    mongo_list_credentials,
    mongo_update_credential,
)
from ext_requests.organization_db import (
    mongo_get_roles_of_user_in_organization,
    user_is_org_member,
)
from flask import request
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
from oakestra_utils.types.statuses import DeploymentStatus, PositiveSchedulingStatus
from resource_abstractor_client import job_operations
from roles.securityUtils import Role, get_jwt_auth_claims, get_jwt_organization

from blueprints.schema_wrapper import SchemaWrapper

logger = logging.getLogger("system_manager")

credentialblp = Blueprint(
    "Credential operations",
    "credential",
    url_prefix="/api/credential",
    description="Operations on a single credential",
)

credentialsblp = Blueprint(
    "Credentials operations",
    "credentials",
    url_prefix="/api/credentials",
    description="Operations on multiple credentials",
)


def _require_credentials_enabled():
    if not _credentials_enabled():
        return abort(
            503,
            description=(
                "Credential subsystem is disabled - set CREDENTIAL_ENCRYPTION_KEY to enable it"
            ),
        )


credentialblp.before_request(_require_credentials_enabled)
credentialsblp.before_request(_require_credentials_enabled)

_create_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string"},
        "scope": {"type": "string", "enum": ["private", "organization"]},
        "metadata": {"type": "object"},
        "data": {"type": "object"},
    },
    "required": ["name", "type", "scope", "data"],
}


def _jwt_principal():
    """Return (username, organization_id, claims) for the current request."""
    return get_jwt_identity(), get_jwt_organization(), get_jwt_auth_claims()


def _is_admin(claims):
    return Role.ADMIN in claims.get("roles", [])


def _check_read_access(record, username, organization_id, claims):
    if _is_admin(claims):
        return True
    if record["scope"] == "private":
        return record["owner_user_id"] == username
    if record["scope"] == "organization":
        if not organization_id or record.get("organization_id") != organization_id:
            return False
        return user_is_org_member(username, organization_id)
    return False


def _check_write_access(record, username, organization_id, claims):
    if _is_admin(claims):
        return True
    if record["owner_user_id"] == username:
        return True
    if (
        record["scope"] == "organization"
        and organization_id
        and record.get("organization_id") == organization_id
    ):
        roles = mongo_get_roles_of_user_in_organization(username, organization_id)
        return Role.ORGANIZATION_ADMIN in roles
    return False


# Job statuses for which a referenced credential must not be deleted: the job is
# either scheduled/being deployed (the credential is still needed for the image
# pull) or already running (it is needed again on restart/replication).
_ACTIVE_JOB_STATUSES = {status.value for status in PositiveSchedulingStatus} | {
    DeploymentStatus.CREATING.value,
    DeploymentStatus.CREATED.value,
    DeploymentStatus.RUNNING.value,
}


def _credential_in_active_use(credential_id):
    """Return True if any active job references this credential.

    The resource abstractor's jobs endpoint does not support arbitrary Mongo
    filters via query params, so fetch all jobs and filter here. Returns None
    if the job list could not be retrieved (callers should fail closed).
    """
    jobs = job_operations.get_jobs()
    if jobs is None:
        return None
    for job in jobs:
        if job.get("status") not in _ACTIVE_JOB_STATUSES:
            continue
        for ref in job.get("credential_refs") or []:
            if ref.get("credential_id") == credential_id:
                return True
    return False


@credentialsblp.route("/")
class CredentialsController(MethodView):
    @credentialsblp.response(200, SchemaWrapper({"type": "array"}), content_type="application/json")
    @jwt_required()
    def get(self, *args, **kwargs):
        """List credentials accessible to the caller.

        Returns the public view (never secret material) of every private
        credential owned by the caller plus every organization-scoped
        credential of an organization the caller belongs to. Requires a JWT
        Bearer token. Returns 503 if the credential subsystem is disabled.
        """
        username, organization_id, claims = _jwt_principal()

        # The `organization` JWT claim is set from the org name supplied at login
        # and is NOT a proof of membership. Re-verify membership here before
        # returning org-scoped credentials, otherwise any authenticated user
        # could enumerate any organization's credential metadata.
        effective_org_id = organization_id
        if effective_org_id and not _is_admin(claims):
            if not user_is_org_member(username, effective_org_id):
                effective_org_id = None

        records = mongo_list_credentials(username, effective_org_id)
        result = []
        for r in records:
            h = registry.get_handler(r["type"])
            if h:
                result.append(h.public_view(r))
        return json_util.dumps(result)


@credentialblp.route("/")
class CredentialCreateController(MethodView):
    @credentialblp.arguments(schema=_create_schema, location="json", validate=False, unknown=True)
    @jwt_required()
    def post(self, *args, **kwargs):
        """Create a credential.

        Body: ``{name, type, scope, metadata?, data}`` where ``data`` holds the
        secret (Fernet-encrypted at rest) and ``scope`` is ``private`` or
        ``organization``. Creating an organization-scoped credential requires the
        Organization_Admin role. Returns 201 with the new ``_id`` on success,
        409 if the name already exists, or 503 if the subsystem is disabled.
        """
        data = request.get_json(silent=True) or {}
        for field in ("name", "type", "scope", "data"):
            if field not in data or data[field] is None:
                return abort(400, description=f"Missing required field: '{field}'")
        if data["scope"] not in ("private", "organization"):
            return abort(400, description="Invalid scope: must be 'private' or 'organization'")

        username, organization_id, claims = _jwt_principal()

        cred_type = data["type"]
        handler = registry.get_handler(cred_type)
        if handler is None:
            return abort(400, description=f"Unknown credential type '{cred_type}'")

        scope = data["scope"]
        if scope == "organization":
            if not organization_id:
                return abort(400, description="organization scope requires an organization context")
            if not _is_admin(claims):
                roles = mongo_get_roles_of_user_in_organization(username, organization_id)
                if Role.ORGANIZATION_ADMIN not in roles:
                    return abort(
                        403,
                        description="organization-scoped credentials require organization admin role",
                    )

        metadata = data.get("metadata") or {}
        payload = data.get("data") or {}

        try:
            handler.validate(payload, metadata)
        except ValueError as e:
            return abort(400, description=str(e))

        record = {
            "name": data["name"],
            "type": cred_type,
            "scope": scope,
            "owner_user_id": username,
            "metadata": metadata,
            "data_ciphertext": encrypt_payload(payload),
        }
        if scope == "organization":
            record["organization_id"] = organization_id

        try:
            credential_id = mongo_create_credential(record)
        except Exception as e:
            if "duplicate" in str(e).lower():
                return abort(409, description="A credential with that name already exists")
            logger.exception("Failed to create credential")
            return abort(500, description="Failed to create credential")

        logger.info(
            f"Credential created: id={credential_id} type={cred_type} scope={scope} user={username}"
        )
        return {"_id": credential_id, "message": "Credential created"}, 201


@credentialblp.route("/<credential_id>")
class CredentialController(MethodView):
    @jwt_required()
    def get(self, credential_id, *args, **kwargs):
        """Get a single credential's public view (no secret material).

        The caller must own the credential, be an Admin, or be a member of the
        owning organization. Returns 403 if not authorized, 404 if not found.
        """
        username, organization_id, claims = _jwt_principal()

        record = mongo_get_credential_by_id(credential_id)
        if record is None:
            return abort(404, description="Credential not found")

        if not _check_read_access(record, username, organization_id, claims):
            return abort(403, description="Access denied")

        handler = registry.get_handler(record["type"])
        if handler is None:
            return abort(500, description=f"Unknown credential type '{record['type']}'")

        return json_util.dumps(handler.public_view(record))

    @jwt_required()
    def put(self, credential_id, *args, **kwargs):
        """Update a credential's metadata and/or secret data.

        Body may contain ``metadata`` (merged into existing) and/or ``data`` (the
        secret, re-encrypted). Requires write access: ownership, Admin, or
        Organization_Admin of the owning organization. Returns 400 if no
        updatable fields are supplied.
        """
        username, organization_id, claims = _jwt_principal()

        record = mongo_get_credential_by_id(credential_id)
        if record is None:
            return abort(404, description="Credential not found")

        if not _check_write_access(record, username, organization_id, claims):
            return abort(403, description="Access denied")

        data = request.get_json() or {}
        handler = registry.get_handler(record["type"])
        if handler is None:
            return abort(500, description=f"Unknown credential type '{record['type']}'")

        updates = {}
        existing_metadata = record.get("metadata", {}) or {}
        if "metadata" in data:
            merged_metadata = {**existing_metadata, **(data["metadata"] or {})}
            updates["metadata"] = merged_metadata
        else:
            merged_metadata = existing_metadata

        if "data" in data:
            payload = data["data"]
            try:
                handler.validate(payload, merged_metadata)
            except ValueError as e:
                return abort(400, description=str(e))
            updates["data_ciphertext"] = encrypt_payload(payload)
        elif "metadata" in data:
            try:
                existing_payload = decrypt_payload(record["data_ciphertext"])
                handler.validate(existing_payload, merged_metadata)
            except ValueError as e:
                return abort(400, description=str(e))

        if not updates:
            return abort(400, description="No updatable fields provided")

        updated = mongo_update_credential(credential_id, updates)
        logger.info(f"Credential updated: id={credential_id} user={username}")
        return json_util.dumps(handler.public_view(updated))

    @jwt_required()
    def delete(self, credential_id, *args, **kwargs):
        """Delete a credential.

        Requires write access (ownership, Admin, or Organization_Admin). Returns
        409 if the credential is still referenced by a scheduled or running job.
        """
        username, organization_id, claims = _jwt_principal()

        record = mongo_get_credential_by_id(credential_id)
        if record is None:
            return abort(404, description="Credential not found")

        if not _check_write_access(record, username, organization_id, claims):
            return abort(403, description="Access denied")

        in_use = _credential_in_active_use(credential_id)
        if in_use is None:
            return abort(503, description="Could not verify credential usage, try again later")
        if in_use:
            return abort(
                409, description="Credential is referenced by active jobs and cannot be deleted"
            )

        mongo_delete_credential(credential_id)
        logger.info(f"Credential deleted: id={credential_id} user={username}")
        return {"message": "Credential deleted"}
