import json

import clients.mqtt_client as mqtt_client
from bson import ObjectId
from oakestra_utils.types.statuses import convert_to_status


def _message(make_message, topic, payload):
    return make_message(topic, json.dumps(payload))


def test_node_information_forwarded_verbatim_including_untagged_fields(
    mqtt_mock, make_message, monkeypatch, contract
):
    payload = contract("node_information")
    captured = {}
    monkeypatch.setattr(
        mqtt_client.candidate_operations,
        "update_candidate_information",
        lambda client_id, data: captured.update(client_id=client_id, data=data) or {"_id": "n1"},
    )
    message = _message(make_message, "nodes/node1/information", payload)
    mqtt_client.handle_mqtt_message(None, None, message)

    assert captured["client_id"] == "node1"
    # untagged Go fields leak onto the wire and are forwarded unfiltered
    assert captured["data"]["Overlay"] is False
    assert captured["data"]["ClusterAddress"] == "0.0.0.0"
    assert captured["data"] == payload


def test_job_status_matches_update_deployed_instance_worker_contract(
    mqtt_mock, make_message, monkeypatch, contract
):
    payload = contract("job_status")
    calls = []
    monkeypatch.setattr(mqtt_client, "update_deployed_instance_worker", lambda *a: calls.append(a))
    message = _message(make_message, "nodes/node1/job", payload)
    mqtt_client.handle_mqtt_message(None, None, message)

    assert calls == [("app.ns.svc.inst", 1, "CREATED", "", "10.0.0.7")]


def test_jobs_resources_matches_update_deployed_instance_job_contract(
    mqtt_mock, make_message, monkeypatch, contract
):
    payload = contract("jobs_resources")
    calls = []
    monkeypatch.setattr(
        mqtt_client,
        "update_deployed_instance_job",
        lambda *a: calls.append(a) or {"ok": 1},
    )
    message = _message(make_message, "nodes/node1/jobs/resources", payload)
    mqtt_client.handle_mqtt_message(None, None, message)

    assert calls == [
        (
            "app.ns.svc.inst",
            1,
            payload["services"][0],
            "node1",
        )
    ]


def test_control_deploy_output_equals_fixture(mqtt_mock, contract):
    fixture = contract("control_deploy")
    # the fixture is the wire format; the caller hands mqtt_publish_edge_deploy
    # the raw Mongo document, complete with a real ObjectId and a string
    # instance number, exactly as job_management does in production
    job = dict(fixture)
    job["_id"] = ObjectId(fixture["_id"])

    mqtt_client.mqtt_publish_edge_deploy("node1", job, "1")

    topic, payload = mqtt_mock.publish.call_args.args
    assert topic == "nodes/node1/control/deploy"
    assert json.loads(payload) == fixture


def test_control_delete_output_equals_fixture(mqtt_mock, contract):
    fixture = contract("control_delete")

    mqtt_client.mqtt_publish_edge_delete(
        "node1", fixture["job_name"], fixture["instance_number"], fixture["virtualization"]
    )

    topic, payload = mqtt_mock.publish.call_args.args
    assert topic == "nodes/node1/control/delete"
    assert json.loads(payload) == fixture


def test_control_error_output_equals_fixture(mqtt_mock, make_message, monkeypatch, contract):
    fixture = contract("control_error")
    monkeypatch.setattr(
        mqtt_client.candidate_operations, "update_candidate_information", lambda *a: None
    )
    message = _message(make_message, "nodes/node1/information", {"ip": "10.0.0.1"})
    mqtt_client.handle_mqtt_message(None, None, message)

    topic, payload = mqtt_mock.publish.call_args.args
    assert topic == "nodes/node1/control/error"
    assert json.loads(payload) == fixture


def test_all_statuses_round_trip_convert_to_status(contract):
    for status in contract("statuses"):
        assert convert_to_status(status).value == status
