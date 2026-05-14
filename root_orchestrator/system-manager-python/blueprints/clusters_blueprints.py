import logging

from bson import json_util
from ext_requests.cluster_requests import cluster_request_to_delete_job_by_ip
from ext_requests.worker_keys_db import mongo_upsert_worker_key
from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint, abort
from oakestra_utils.types.statuses import convert_to_status
from resource_abstractor_client import candidate_operations
from services.instance_management import update_job_status
from utils.network import sanitize

logger = logging.getLogger("system_manager")

clustersbp = Blueprint("Clusters", "cluster management", url_prefix="/api/clusters")
clusterinfo = Blueprint("Clusterinfo", "cluster informations", url_prefix="/api/information")

cluster_info_schema = {
    "type": "object",
    "properties": {
        "cpu_percent": {"type": "string"},
        "cpu_cores": {"type": "string"},
        "gpu_cores": {"type": "string"},
        "gpu_percent": {"type": "string"},
        "cumulative_memory_in_mb": {"type": "string"},
        "number_of_nodes": {"type": "string"},
        "virtualization": {"type": "array", "items": {"type": "string"}},
        "csi_drivers": {"type": "array", "items": {"type": "string"}},
        "more": {"type": "object"},
        "worker_groups": {"type": "string"},
        "supported_addons": {"type": "array", "items": {"type": "string"}},
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "_id": {"type": "string"},
                    "status": {"type": "string"},
                    "instance_list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "instance_number": {"type": "string"},
                                "status": {"type": "string"},
                                "status_detail": {"type": "string"},
                                "publicip": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


@clustersbp.route("/")
class ClustersController(MethodView):
    def get(self, *args, **kwargs):
        clusters = list(
            map(
                map_cluster_attributes,
                candidate_operations.get_candidates(resources="last_modified_timestamp,active"),
            )
        )
        if clusters is None:
            return abort(500, "Getting clusters failed")
        return json_util.dumps(clusters)


@clustersbp.route("/active")
class ActiveClustersController(MethodView):
    def get(self, *args, **kwargs):
        clusters = list(
            map(
                map_cluster_attributes,
                candidate_operations.get_candidates(
                    active=True, resources="last_modified_timestamp,active"
                ),
            )
        )
        if clusters is None:
            return abort(500, "Getting clusters failed")
        return json_util.dumps(clusters)


@clusterinfo.route("/<clusterid>")
class ClusterController(MethodView):
    @clusterinfo.arguments(
        schema=cluster_info_schema, location="json", validate=False, unknown=True
    )
    def post(self, *args, **kwargs):
        data = request.json
        cluster_id = kwargs["clusterid"]

        cluster = candidate_operations.get_candidate_by_id(cluster_id)
        if cluster is None:
            return abort(401, "Unknown cluster")
        # IP matching provides lightweight caller verification: a rogue process cannot
        # trivially claim to be cluster X unless it shares that cluster's egress IP.
        # Limitation: this breaks with NAT, load-balancers, or container-network proxies
        # that rewrite the source address. The long-term fix is a signed token issued
        # during the gRPC registration handshake (mTLS or a shared bearer token). Until
        # then we intentionally use remote_addr (not X-Forwarded-For, which is spoofable).
        registered_ip = cluster.get("ip", "")
        caller_ip = sanitize(request.remote_addr)
        if registered_ip and caller_ip != registered_ip:
            logger.warning(
                f"Cluster update IP mismatch: cluster_id={cluster_id} "
                f"registered={registered_ip} caller={caller_ip}"
            )
            return abort(403, "Caller IP does not match registered cluster IP")

        jobs = data.get("jobs")
        logger.info(f"Received cluster update for {cluster_id}: {data}")
        del data["jobs"]

        worker_keys = data.pop("worker_keys", {})
        for worker_id, key_info in worker_keys.items():
            pub_keyset_b64 = key_info.get("pub_keyset_b64")
            key_id = key_info.get("key_id")
            if pub_keyset_b64 and key_id:
                mongo_upsert_worker_key(worker_id, cluster_id, pub_keyset_b64, key_id)
        # Prevent the IP address from being overwritten by cluster updates
        # The IP is set during initial registration and should not change
        if "ip" in data:
            logger.warning(
                "Cluster update attempted to change IP address, ignoring update to prevent corruption"
            )
            del data["ip"]
        updated_cluster = candidate_operations.update_candidate_information(cluster_id, data)
        if updated_cluster is None:
            logger.error("Could not update cluster")
            return abort(400, "Updating cluster failed")

        # TODO(GB): fire an event to react to the cluster update
        # and move this logic somewhere else.
        for j in jobs:
            result = update_job_status(
                job_id=j.get("_id"),
                status=convert_to_status(j.get("status")),
                status_detail=j.get("status_detail"),
                instances=j.get("instance_list"),
            )
            if result is None:
                # cluster has outdated jobs, ask to undeploy
                addr = sanitize(request.remote_addr)
                cluster_request_to_delete_job_by_ip(j.get("_id"), -1, addr)

        return "ok"


# Map candidate attributes to cluster attributes for compatibility with ext tools
# Deprecation note: this mapping should be removed when ext tools are updated to use
def map_cluster_attributes(x):
    x["cluster_name"] = x.get("candidate_name", "Unknown Cluster")
    x["cluster_location"] = x.get("candidate_location", "Unknown Location")

    x["aggregated_cpu_percent"] = x.get("cpu_percent", 0)
    x["memory_in_mb"] = x.get("memory", 0)
    x["total_cpu_cores"] = x.get("vcpus", 0)
    x["total_gpu_cores"] = x.get("vgpus", 0)
    return x
