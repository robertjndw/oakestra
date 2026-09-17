import json
import time

import clients.mqtt_client as mqtt_client
import pytest
from bson import ObjectId

pytestmark = pytest.mark.integration


def _wait_until(predicate, timeout=5, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def test_unknown_node_publishes_control_error_over_wire(cm_client, peer, node_id, monkeypatch):
    monkeypatch.setattr(
        mqtt_client.candidate_operations, "update_candidate_information", lambda *a: None
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    message = peer.expect(f"nodes/{node_id}/control/error")
    assert json.loads(message.payload) == {"message": "Node not registered to the cluster"}
    assert message.qos == 0


def test_known_node_publishes_nothing(cm_client, peer, node_id, monkeypatch):
    monkeypatch.setattr(
        mqtt_client.candidate_operations, "update_candidate_information", lambda *a: {"_id": "n"}
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    peer.expect_none()


def test_job_status_calls_handler_with_exact_args(cm_client, peer, node_id, monkeypatch, contract):
    payload = contract("job_status")
    calls = []
    monkeypatch.setattr(mqtt_client, "update_deployed_instance_worker", lambda *a: calls.append(a))

    peer.publish(f"nodes/{node_id}/job", payload)

    _wait_until(lambda: calls)
    assert calls == [("app.ns.svc.inst", 1, "CREATED", "", "10.0.0.7")]


def test_stale_resources_publishes_control_delete_over_wire(
    cm_client, peer, node_id, monkeypatch, contract
):
    payload = contract("jobs_resources")
    monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", lambda *a: None)

    peer.publish(f"nodes/{node_id}/jobs/resources", payload)

    message = peer.expect(f"nodes/{node_id}/control/delete")
    assert json.loads(message.payload) == {
        "job_name": "app.ns.svc.inst",
        "virtualization": "docker",
        "instance_number": 1,
    }
    assert message.qos == 0


def test_subscribes_to_exactly_three_topics(cm_client, peer, node_id, monkeypatch):
    hits = []

    def record(name, result):
        def _handler(*args, **kwargs):
            hits.append(name)
            return result

        return _handler

    monkeypatch.setattr(
        mqtt_client.candidate_operations,
        "update_candidate_information",
        record("information", {"_id": "n"}),
    )
    monkeypatch.setattr(mqtt_client, "update_deployed_instance_worker", record("job", {"ok": 1}))
    monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", record("resources", {"ok": 1}))

    positives = {
        f"nodes/{node_id}/information": {"ip": "10.0.0.1"},
        f"nodes/{node_id}/job": {"sname": "x", "status": "CREATED"},
        f"nodes/{node_id}/jobs/resources": {"services": [{"job_name": "x", "instance": 1}]},
    }
    negatives = [
        f"nodes/{node_id}/control/deploy",
        f"nodes/{node_id}/information/extra",
        f"nodes/{node_id}/jobs",
        f"other/{node_id}/information",
        f"nodes/{node_id}/net/service/deployed",
    ]

    for topic, payload in positives.items():
        peer.publish(topic, payload)
    for topic in negatives:
        peer.publish(topic, {})

    _wait_until(lambda: len(hits) >= 3)
    # give any (wrongly) matching negative a moment to arrive too
    time.sleep(0.3)
    assert sorted(hits) == ["information", "job", "resources"]


def test_edge_deploy_wire_format_equals_fixture(cm_client, peer, node_id, contract):
    fixture = contract("control_deploy")
    job = dict(fixture)
    job["_id"] = ObjectId(fixture["_id"])

    mqtt_client.mqtt_publish_edge_deploy(node_id, job, "1")

    message = peer.expect(f"nodes/{node_id}/control/deploy")
    assert json.loads(message.payload) == fixture
    assert message.qos == 0


def test_edge_delete_wire_format_equals_fixture(cm_client, peer, node_id, contract):
    fixture = contract("control_delete")

    mqtt_client.mqtt_publish_edge_delete(
        node_id, fixture["job_name"], fixture["instance_number"], fixture["virtualization"]
    )

    message = peer.expect(f"nodes/{node_id}/control/delete")
    assert json.loads(message.payload) == fixture
    assert message.qos == 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "paho-mqtt 1.6.1 runs with suppress_exceptions=False; handle_mqtt_message does "
        "json.loads() before any topic check, so one malformed payload raises out of "
        "on_message and kills the loop_start() background thread. CM stops processing "
        "MQTT for the rest of the process. Strict so this starts failing the moment "
        "that thread is made resilient."
    ),
)
# paho re-raises the JSONDecodeError from its background thread, which pytest
# reports as an unraisable-exception warning. That is the failure this test
# documents, not a leak.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_malformed_payload_then_valid_message_is_still_processed(
    cm_client, peer, node_id, monkeypatch
):
    peer.publish_raw(f"nodes/{node_id}/information", b"not-json")
    time.sleep(0.5)  # let the (dying) loop thread actually process the bad message

    calls = []
    monkeypatch.setattr(
        mqtt_client.candidate_operations,
        "update_candidate_information",
        lambda *a: calls.append(a) or {"ok": 1},
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    _wait_until(lambda: calls, timeout=3)
