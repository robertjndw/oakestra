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
from ext_requests.organization_db import mongo_get_roles_of_user_in_organization
from flask import request
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
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
        return abort(503, description="Credential subsystem is disabled — set CREDENTIAL_ENCRYPTION_KEY to enable it")


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
        roles = mongo_get_roles_of_user_in_organization(username, organization_id)
        return bool(roles)
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


@credentialsblp.route("/")
class CredentialsController(MethodView):
    @credentialsblp.response(200, SchemaWrapper({"type": "array"}), content_type="application/json")
    @jwt_required()
    def get(self, *args, **kwargs):
        username = get_jwt_identity()
        organization_id = get_jwt_organization()
        claims = get_jwt_auth_claims()

        # The `organization` JWT claim is set from the org name supplied at login
        # and is NOT a proof of membership. Re-verify membership here before
        # returning org-scoped credentials, otherwise any authenticated user
        # could enumerate any organization's credential metadata.
        effective_org_id = organization_id
        if effective_org_id and not _is_admin(claims):
            roles = mongo_get_roles_of_user_in_organization(username, effective_org_id)
            if not roles:
                effective_org_id = None

        records = mongo_list_credentials(username, effective_org_id)
        handler_map = {}
        result = []
        for r in records:
            h = handler_map.get(r["type"]) or registry.get_handler(r["type"])
            if h:
                handler_map[r["type"]] = h
                result.append(h.public_view(r))
        return json_util.dumps(result)


@credentialblp.route("/")
class CredentialCreateController(MethodView):
    @credentialblp.arguments(schema=_create_schema, location="json", validate=False, unknown=True)
    @jwt_required()
    def post(self, *args, **kwargs):
        data = request.get_json()
        username = get_jwt_identity()
        organization_id = get_jwt_organization()
        claims = get_jwt_auth_claims()

        cred_type = data.get("type")
        handler = registry.get_handler(cred_type)
        if handler is None:
            return abort(400, description=f"Unknown credential type '{cred_type}'")

        scope = data.get("scope")
        if scope == "organization":
            if not organization_id:
                return abort(400, description="organization scope requires an organization context")
            if not _is_admin(claims):
                roles = mongo_get_roles_of_user_in_organization(username, organization_id)
                if Role.ORGANIZATION_ADMIN not in roles:
                    return abort(403, description="organization-scoped credentials require organization admin role")

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

        logger.info(f"Credential created: id={credential_id} type={cred_type} scope={scope} user={username}")
        return {"_id": credential_id, "message": "Credential created"}, 201


@credentialblp.route("/<credential_id>")
class CredentialController(MethodView):
    @jwt_required()
    def get(self, credential_id, *args, **kwargs):
        username = get_jwt_identity()
        organization_id = get_jwt_organization()
        claims = get_jwt_auth_claims()

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
        username = get_jwt_identity()
        organization_id = get_jwt_organization()
        claims = get_jwt_auth_claims()

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
        username = get_jwt_identity()
        organization_id = get_jwt_organization()
        claims = get_jwt_auth_claims()

        record = mongo_get_credential_by_id(credential_id)
        if record is None:
            return abort(404, description="Credential not found")

        if not _check_write_access(record, username, organization_id, claims):
            return abort(403, description="Access denied")

        active_jobs = job_operations.get_jobs(
            **{"credential_refs.credential_id": credential_id, "status": "RUNNING"}
        )
        if active_jobs:
            return abort(409, description="Credential is referenced by active jobs and cannot be deleted")

        mongo_delete_credential(credential_id)
        logger.info(f"Credential deleted: id={credential_id} user={username}")
        return {"message": "Credential deleted"}
