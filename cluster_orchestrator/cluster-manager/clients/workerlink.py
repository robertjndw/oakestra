import json
import logging

from oakestra_messaging.bus import Message
from oakestra_messaging.factory import from_env, mqtt_tls_from_env
from oakestra_utils.types.statuses import convert_to_status
from resource_abstractor_client import candidate_operations

from clients.job_management import (
    delete_job_instance,
    update_deployed_instance_job,
    update_deployed_instance_worker,
)

logger = logging.getLogger("cluster_manager")

_bus = None


def bus_from_env():
    return from_env(
        qos=0,
        tls=mqtt_tls_from_env("cluster", "CLUSTER_KEYFILE_PASSWORD"),
        logger=logger,
    )


def start(bus):
    global _bus
    _bus = bus
    bus.subscribe("nodes/+/information", _on_information)
    bus.subscribe("nodes/+/job", _on_job_status)
    bus.subscribe("nodes/+/jobs/resources", _on_job_resources)


def _on_information(message: Message):
    node_id = message.topic.split("/")[1]
    payload = json.loads(message.payload)
    logger.info("MQTT - Received from worker: ")
    logger.info(dict(topic=message.topic, payload=payload))

    payload = {k: v for k, v in payload.items() if v is not None}
    updated = candidate_operations.update_candidate_information(node_id, payload)
    if updated is None:
        _bus.publish(
            "nodes/" + node_id + "/control/error",
            json.dumps({"message": "Node not registered to the cluster"}),
        )


def _on_job_status(message: Message):
    # unlike the other two handlers, the job report itself carries the job
    # name, so the node id segment of the topic is not needed here
    payload = json.loads(message.payload)
    logger.info("MQTT - Received from worker: ")
    logger.info(dict(topic=message.topic, payload=payload))

    job_name = payload.get("sname")
    status = convert_to_status(payload.get("status"))
    status_detail = payload.get("status_detail", None)
    instance = payload.get("instance")
    publicip = payload.get("publicip", "--")
    update_deployed_instance_worker(job_name, instance, status.value, status_detail, publicip)


def _on_job_resources(message: Message):
    node_id = message.topic.split("/")[1]
    payload = json.loads(message.payload)
    logger.info("MQTT - Received from worker: ")
    logger.info(dict(topic=message.topic, payload=payload))

    services = payload["services"]
    for service in services:
        try:
            # If unable to update then worker has outdated information
            # and service must be undeployed
            if (
                update_deployed_instance_job(
                    service.get("job_name"), service.get("instance", 0), service, node_id
                )
                is None
            ):
                publish_delete(
                    node_id,
                    service.get("job_name"),
                    service.get("instance"),
                    service.get("virtualization"),
                )
        except Exception as e:
            logger.error("MQTT - unable to update service resources")
            logger.error(e)


def publish_deploy(worker_id, job, instance_number):
    topic = "nodes/" + worker_id + "/control/deploy"
    data = job
    data["instance_number"] = int(instance_number)
    job_id = str(job.get("_id"))  # serialize ObjectId to string
    job.__setitem__("_id", job_id)
    _bus.publish(topic, json.dumps(data))  # MQTT cannot send JSON, dump it to String here


def publish_delete(worker_id, job_name, instance_number, runtime="docker"):
    topic = "nodes/" + worker_id + "/control/delete"
    data = {
        "job_name": job_name,
        "virtualization": runtime,
        "instance_number": int(instance_number),
    }
    _bus.publish(topic, json.dumps(data))


def undeploy_instance(job_id, instance_number, erase=True):
    return delete_job_instance(job_id, instance_number, erase, notify_worker=publish_delete)
