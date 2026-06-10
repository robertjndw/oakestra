import logging

import requests
from oakestra_utils.types.statuses import DeploymentStatus
from resource_abstractor_client import candidate_operations, job_operations
from services.cluster_management import find_cluster_of_job
from utils.network import sanitize

logger = logging.getLogger("system_manager")


def cluster_request_status(cluster_id):
    cluster = candidate_operations.get_candidate_by_id(cluster_id)
    try:
        cluster_addr = "http://" + cluster.get("ip") + ":" + str(cluster.get("port")) + "/status"
        requests.get(cluster_addr, timeout=5)
    except requests.exceptions.RequestException:
        logger.error("Calling Cluster Orchestrator /status not successful.")


def _embed_credentials(job: dict) -> None:
    """
    Resolve, decrypt, and materialize any credential_refs on the job, embedding
    the plaintext values into job["credentials"] for delivery to the cluster/worker.
    Raises RuntimeError if any ref fails - the caller should abort the deploy so
    the user gets a clear error instead of a silent pull failure at the worker.
    """
    from credentials.crypto import is_enabled
    from credentials.resolver import CredentialError, materialize_credential

    credential_refs = job.get("credential_refs", [])
    if not credential_refs:
        return
    if not is_enabled():
        raise RuntimeError(
            "Job has credential_refs but the credential subsystem is disabled - "
            "set CREDENTIAL_ENCRYPTION_KEY to enable it"
        )

    materialized = []
    for ref in credential_refs:
        try:
            materialized.append(materialize_credential(ref["credential_id"], ref["use_as"]))
        except CredentialError as e:
            raise RuntimeError(
                f"Failed to materialize credential {ref.get('credential_id')}: {e}"
            ) from e
    job["credentials"] = materialized


def cluster_request_to_deploy(cluster_id, job_id, instance_number):
    cluster = candidate_operations.get_candidate_by_id(cluster_id)
    if cluster is None:
        logger.error(f"Cluster with {cluster_id} not found.")
        return

    job = job_operations.get_job_instance(job_id, instance_number)
    if job is None:
        logger.error(f"Job with {job_id} not found.")
        return

    job["_id"] = str(job["_id"])
    try:
        _embed_credentials(job)
    except RuntimeError as e:
        logger.error(f"Cannot deploy job {job_id} instance {instance_number}: {e}")
        # Surface the failure on the job so the user sees why nothing was deployed
        # instead of the job silently staying in its scheduled state.
        job_operations.update_job_status(job_id, DeploymentStatus.FAILED, status_detail=str(e))
        return

    cluster_addr = (
        "http://"
        + sanitize(cluster.get("ip"), request=True)
        + ":"
        + str(cluster.get("port"))
        + "/api/service/"
        + str(job_id)
        + "/"
        + str(instance_number)
    )
    try:
        logger.debug(
            f"Preparing deploy request for job {job_id} instance {instance_number} to cluster {cluster_addr}"
        )
        logger.info(f"Deploy request to {cluster_addr}")
        requests.post(cluster_addr, json=job, timeout=10)
    except Exception as e:
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} not successful: {e}")


def cluster_request_to_delete_job(job_id, instance_number):
    cluster = find_cluster_of_job(job_id, int(instance_number))
    if cluster is None:
        logger.error(f"Cluster for job {job_id} not found.")
        return

    try:
        cluster_addr = (
            "http://"
            + sanitize(cluster.get("ip"), request=True)
            + ":"
            + str(cluster.get("port"))
            + "/api/service/"
            + str(job_id)
            + "/"
            + str(instance_number)
        )
        logger.info(f"Delete request to {cluster_addr}")
        requests.delete(cluster_addr, timeout=10)
    except Exception as e:
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} job not successful: {e}")


def cluster_request_to_delete_job_by_ip(job_id, instance_number, ip):
    try:
        cluster = candidate_operations.get_candidate_by_ip(ip)
        if cluster is None:
            logger.error(f"Cluster with {ip} not found")
            return

        cluster_addr = (
            "http://"
            + sanitize(cluster.get("ip"), request=True)
            + ":"
            + str(cluster.get("port"))
            + "/api/service/"
            + str(job_id)
            + "/"
            + str(instance_number)
        )
        logger.info(f"Delete request to {cluster_addr}")
        requests.delete(cluster_addr, timeout=10)
    except Exception as e:
        logger.error(e)
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} job by ip not successful.")


def cluster_request_to_replicate_up(cluster_obj, job_obj, int_replicas):
    try:
        _embed_credentials(job_obj)
    except RuntimeError as e:
        logger.error(f"Cannot replicate job: {e}")
        job_id = job_obj.get("_id")
        if job_id:
            job_operations.update_job_status(
                str(job_id), DeploymentStatus.FAILED, status_detail=str(e)
            )
        return

    cluster_addr = (
        "http://"
        + sanitize(cluster_obj.get("ip"), request=True)
        + ":"
        + str(cluster_obj.get("port"))
        + "/api/replicate/"
    )
    try:
        requests.post(cluster_addr, json={"job": job_obj, "int_replicas": int_replicas}, timeout=10)
        return 1
    except requests.exceptions.RequestException:
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} /api/replicate not successful.")


def cluster_request_to_replicate_down(cluster_obj, job_obj, int_replicas):
    cluster_addr = (
        "http://"
        + sanitize(cluster_obj.get("ip"), request=True)
        + ":"
        + str(cluster_obj.get("port"))
        + "/api/replicate/"
    )
    try:
        requests.post(cluster_addr, json={"job": job_obj, "int_replicas": int_replicas}, timeout=10)
        return 1
    except requests.exceptions.RequestException:
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} /api/replicate not successful.")


def cluster_request_to_move_within_cluster(cluster_obj, job_id, node_from, node_to):
    cluster_addr = (
        "http://"
        + sanitize(cluster_obj.get("ip"), request=True)
        + ":"
        + str(cluster_obj.get("port"))
        + "/api/move/"
    )
    try:
        requests.post(
            cluster_addr,
            json={"job": job_id, "node_from": node_from, "node_to": node_to},
            timeout=10,
        )
        return 1
    except requests.exceptions.RequestException:
        logger.error(f"Calling Cluster Orchestrator {cluster_addr} /api/move not successful.")
