import json
import time

import clients.workerlink as workerlink
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


def test_unknown_node_publishes_control_error_over_wire(cm_bus, peer, node_id, monkeypatch):
    monkeypatch.setattr(
        workerlink.candidate_operations, "update_candidate_information", lambda *a: None
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    message = peer.expect(f"nodes/{node_id}/control/error")
    assert json.loads(message.payload) == {"message": "Node not registered to the cluster"}
    assert message.qos == 0


def test_known_node_publishes_nothing(cm_bus, peer, node_id, monkeypatch):
    monkeypatch.setattr(
        workerlink.candidate_operations, "update_candidate_information", lambda *a: {"_id": "n"}
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    peer.expect_none()


def test_job_status_calls_handler_with_exact_args(cm_bus, peer, node_id, monkeypatch, contract):
    payload = contract("job_status")
    calls = []
    monkeypatch.setattr(workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a))

    peer.publish(f"nodes/{node_id}/job", payload)

    _wait_until(lambda: calls)
    assert calls == [("app.ns.svc.inst", 1, "CREATED", "", "10.0.0.7")]


def test_stale_resources_publishes_control_delete_over_wire(
    cm_bus, peer, node_id, monkeypatch, contract
):
    payload = contract("jobs_resources")
    monkeypatch.setattr(workerlink, "update_deployed_instance_job", lambda *a: None)

    peer.publish(f"nodes/{node_id}/jobs/resources", payload)

    message = peer.expect(f"nodes/{node_id}/control/delete")
    assert json.loads(message.payload) == {
        "job_name": "app.ns.svc.inst",
        "virtualization": "docker",
        "instance_number": 1,
    }
    assert message.qos == 0


def test_subscribes_to_exactly_three_topics(cm_bus, peer, node_id, monkeypatch):
    hits = []

    def record(name, result):
        def _handler(*args, **kwargs):
            hits.append(name)
            return result

        return _handler

    monkeypatch.setattr(
        workerlink.candidate_operations,
        "update_candidate_information",
        record("information", {"_id": "n"}),
    )
    monkeypatch.setattr(workerlink, "update_deployed_instance_worker", record("job", {"ok": 1}))
    monkeypatch.setattr(workerlink, "update_deployed_instance_job", record("resources", {"ok": 1}))

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


def test_edge_deploy_wire_format_equals_fixture(cm_bus, peer, node_id, contract):
    fixture = contract("control_deploy")
    job = dict(fixture)
    job["_id"] = ObjectId(fixture["_id"])

    workerlink.publish_deploy(node_id, job, "1")

    message = peer.expect(f"nodes/{node_id}/control/deploy")
    assert json.loads(message.payload) == fixture
    assert message.qos == 0


def test_edge_delete_wire_format_equals_fixture(cm_bus, peer, node_id, contract):
    fixture = contract("control_delete")

    workerlink.publish_delete(
        node_id, fixture["job_name"], fixture["instance_number"], fixture["virtualization"]
    )

    message = peer.expect(f"nodes/{node_id}/control/delete")
    assert json.loads(message.payload) == fixture
    assert message.qos == 0


def test_malformed_payload_then_valid_message_is_still_processed(
    cm_bus, peer, node_id, monkeypatch
):
    peer.publish_raw(f"nodes/{node_id}/information", b"not-json")
    time.sleep(0.5)  # let the broker deliver and the dispatcher log the failure

    calls = []
    monkeypatch.setattr(
        workerlink.candidate_operations,
        "update_candidate_information",
        lambda *a: calls.append(a) or {"ok": 1},
    )
    peer.publish(f"nodes/{node_id}/information", {"ip": "10.0.0.1"})

    _wait_until(lambda: calls, timeout=3)
