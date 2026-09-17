import json

import clients.workerlink as workerlink
from bson import ObjectId
from oakestra_utils.types.statuses import convert_to_status


def test_node_information_forwarded_verbatim_including_untagged_fields(bus, monkeypatch, contract):
    payload = contract("node_information")
    captured = {}
    monkeypatch.setattr(
        workerlink.candidate_operations,
        "update_candidate_information",
        lambda client_id, data: captured.update(client_id=client_id, data=data) or {"_id": "n1"},
    )
    bus.deliver("nodes/node1/information", json.dumps(payload))

    assert captured["client_id"] == "node1"
    # untagged Go fields leak onto the wire and are forwarded unfiltered
    assert captured["data"]["Overlay"] is False
    assert captured["data"]["ClusterAddress"] == "0.0.0.0"
    assert captured["data"] == payload


def test_job_status_matches_update_deployed_instance_worker_contract(bus, monkeypatch, contract):
    payload = contract("job_status")
    calls = []
    monkeypatch.setattr(workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a))
    bus.deliver("nodes/node1/job", json.dumps(payload))

    assert calls == [("app.ns.svc.inst", 1, "CREATED", "", "10.0.0.7")]


def test_jobs_resources_matches_update_deployed_instance_job_contract(bus, monkeypatch, contract):
    payload = contract("jobs_resources")
    calls = []
    monkeypatch.setattr(
        workerlink,
        "update_deployed_instance_job",
        lambda *a: calls.append(a) or {"ok": 1},
    )
    bus.deliver("nodes/node1/jobs/resources", json.dumps(payload))

    assert calls == [
        (
            "app.ns.svc.inst",
            1,
            payload["services"][0],
            "node1",
        )
    ]


def test_control_deploy_output_equals_fixture(bus, contract):
    fixture = contract("control_deploy")
    # the fixture is the wire format; the caller hands publish_deploy the raw
    # Mongo document, complete with a real ObjectId and a string instance
    # number, exactly as job_management does in production
    job = dict(fixture)
    job["_id"] = ObjectId(fixture["_id"])

    workerlink.publish_deploy("node1", job, "1")

    message = bus.published[-1]
    assert message.topic == "nodes/node1/control/deploy"
    assert json.loads(message.payload) == fixture


def test_control_delete_output_equals_fixture(bus, contract):
    fixture = contract("control_delete")

    workerlink.publish_delete(
        "node1", fixture["job_name"], fixture["instance_number"], fixture["virtualization"]
    )

    message = bus.published[-1]
    assert message.topic == "nodes/node1/control/delete"
    assert json.loads(message.payload) == fixture


def test_control_error_output_equals_fixture(bus, monkeypatch, contract):
    fixture = contract("control_error")
    monkeypatch.setattr(
        workerlink.candidate_operations, "update_candidate_information", lambda *a: None
    )
    bus.deliver("nodes/node1/information", json.dumps({"ip": "10.0.0.1"}))

    message = bus.published[-1]
    assert message.topic == "nodes/node1/control/error"
    assert json.loads(message.payload) == fixture


def test_all_statuses_round_trip_convert_to_status(contract):
    for status in contract("statuses"):
        assert convert_to_status(status).value == status
