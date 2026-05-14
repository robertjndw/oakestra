import logging

from bson import json_util
from flask import request
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort
from resource_abstractor_client import candidate_operations, job_operations
from resource_abstractor_client.job_operations import get_job_instance
from roles.securityUtils import Role, get_jwt_auth_claims, get_jwt_organization

from blueprints.schema_wrapper import SchemaWrapper
from credentials import registry
from credentials.crypto import encrypt_payload, is_enabled as _credentials_enabled
from credentials.resolver import (
    CredentialError,
    CredentialNotFoundError,
    CredentialPermissionError,
    WorkerKeyNotFoundError,
    resolve_credential_ref,
    seal_credential_for_worker,
)
from ext_requests.credentials_db import (
    mongo_create_credential,
    mongo_delete_credential,
    mongo_get_credential_by_id,
    mongo_list_credentials,
    mongo_update_credential,
)
from ext_requests.organization_db import mongo_get_roles_of_user_in_organization
from ext_requests.worker_keys_db import mongo_get_worker_key
from utils.network import sanitize

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

_update_schema = {
    "type": "object",
    "properties": {
        "metadata": {"type": "object"},
        "data": {"type": "object"},
    },
}

_seal_schema = {
    "type": "object",
    "properties": {
        "credential_id": {"type": "string"},
        "use_as": {"type": "string"},
        "worker_id": {"type": "string"},
        "job_id": {"type": "string"},
        "instance_number": {"type": "integer"},
    },
    "required": ["credential_id", "use_as", "worker_id", "job_id", "instance_number"],
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
    if record["scope"] == "organization" and organization_id:
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
        records = mongo_list_credentials(username, organization_id)
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
            return abort(500, description=str(e))

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
        if "metadata" in data:
            updates["metadata"] = data["metadata"]
        if "data" in data:
            payload = data["data"]
            metadata = data.get("metadata") or record.get("metadata", {})
            try:
                handler.validate(payload, metadata)
            except ValueError as e:
                return abort(400, description=str(e))
            updates["data_ciphertext"] = encrypt_payload(payload)

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

        # Check if any active job references this credential
        active_jobs = job_operations.get_jobs(
            **{"credential_refs.credential_id": credential_id, "status": "RUNNING"}
        )
        if active_jobs:
            return abort(409, description="Credential is referenced by active jobs and cannot be deleted")

        mongo_delete_credential(credential_id)
        logger.info(f"Credential deleted: id={credential_id} user={username}")
        return {"message": "Credential deleted"}


@credentialblp.route("/seal")
class CredentialSealController(MethodView):
    @credentialblp.arguments(schema=_seal_schema, location="json", validate=False, unknown=True)
    def post(self, *args, **kwargs):
        """
        Cluster-to-root callback: seal a credential for a specific worker.
        Auth: X-Cluster-Id header must match a registered cluster.
        """
        cluster_id = request.headers.get("X-Cluster-Id")
        if not cluster_id:
            return abort(401, description="X-Cluster-Id header required")

        cluster = candidate_operations.get_candidate_by_id(cluster_id)
        if cluster is None:
            return abort(401, description="Unknown cluster")

        # Verify the caller's IP matches the registered cluster address so that a
        # cluster cannot seal credentials for a different cluster's workers.
        # Same limitation as /api/information: breaks with NAT/proxies. The right
        # long-term fix is a token issued during the gRPC handshake. Until then we
        # use remote_addr (not X-Forwarded-For, which is caller-controlled).
        registered_ip = cluster.get("ip", "")
        caller_ip = sanitize(request.remote_addr)
        if registered_ip and caller_ip != registered_ip:
            logger.warning(
                f"Seal request IP mismatch: cluster_id={cluster_id} "
                f"registered={registered_ip} caller={caller_ip}"
            )
            return abort(403, description="Caller IP does not match registered cluster IP")

        data = request.get_json()
        credential_id = data["credential_id"]
        use_as = data["use_as"]
        worker_id = data["worker_id"]
        job_id = data["job_id"]
        instance_number = int(data["instance_number"])

        # Verify the target worker belongs to the calling cluster.
        worker_key_record = mongo_get_worker_key(worker_id)
        if worker_key_record is None or worker_key_record.get("cluster_id") != cluster_id:
            return abort(403, description="Worker does not belong to this cluster")

        # Verify job exists and references this credential
        job = job_operations.get_job_by_id(job_id)
        if job is None:
            return abort(404, description="Job not found")

        credential_refs = job.get("credential_refs", [])
        if not any(
            ref.get("credential_id") == credential_id and ref.get("use_as") == use_as
            for ref in credential_refs
        ):
            return abort(403, description="Credential not referenced by this job")

        # Verify that this specific instance is actually scheduled on the requested worker.
        # Root learns the per-instance worker_id via the cluster's 15s aggregation push.
        # If the record is absent (race at initial scheduling) we allow the seal through
        # but log a warning; once the aggregation push arrives, subsequent requests will
        # be validated strictly.
        instance_record = get_job_instance(job_id, instance_number)
        if instance_record is not None:
            scheduled_worker = instance_record.get("worker_id")
            if scheduled_worker and scheduled_worker != worker_id:
                logger.warning(
                    f"Seal request worker mismatch: job={job_id} instance={instance_number} "
                    f"scheduled_on={scheduled_worker} requested_for={worker_id}"
                )
                return abort(403, description="Instance is not scheduled on the requested worker")

        try:
            sealed = seal_credential_for_worker(
                credential_id=credential_id,
                use_as=use_as,
                worker_id=worker_id,
                job_id=job_id,
                instance_number=instance_number,
            )
        except CredentialNotFoundError as e:
            return abort(404, description=str(e))
        except WorkerKeyNotFoundError as e:
            return abort(409, description=str(e))
        except CredentialError as e:
            return abort(400, description=str(e))

        return sealed, 200
