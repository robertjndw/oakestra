import json
import logging

import clients.mqtt_client as mqtt_client
import pytest


def _message(make_message, topic, payload):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return make_message(topic, body)


class TestHandleConnect:
    def test_handle_connect_subscribes_three_topics_qos_default(self, mqtt_mock):
        mqtt_client.handle_connect(None, None, None, 0)

        # positional args only: subscribe() defaults to QoS 0, the calls never pass qos=
        assert mqtt_mock.subscribe.call_args_list == [
            (("nodes/+/information",),),
            (("nodes/+/job",),),
            (("nodes/+/jobs/resources",),),
        ]


class TestHandleLogging:
    def test_handle_logging_only_logs_on_string_level(self, caplog):
        # real paho passes an int level here (MQTT_LOG_ERR == 16), never the string
        # "MQTT_LOG_ERR", so this branch never fires in production. Both cases are
        # covered so the dead check stays visible.
        with caplog.at_level(logging.INFO, logger="cluster_manager"):
            mqtt_client.handle_logging(None, None, "MQTT_LOG_ERR", b"boom")
        assert "Error: b'boom'" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="cluster_manager"):
            mqtt_client.handle_logging(None, None, 16, b"boom")
        assert caplog.text == ""


class TestDispatch:
    def test_non_json_payload_raises_before_dispatch(self, mqtt_mock, make_message):
        message = make_message("nodes/n1/information", "not-json")
        with pytest.raises(json.JSONDecodeError):
            mqtt_client.handle_mqtt_message(None, None, message)

    def test_unknown_topic_valid_json_is_ignored(self, mqtt_mock, make_message):
        message = _message(make_message, "nodes/n1/unknown", {})
        # must not raise and must not touch the broker
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_not_called()

    def test_client_id_is_second_segment_even_with_extra_segments(
        self, mqtt_mock, make_message, monkeypatch
    ):
        # the information regex has no trailing segment restriction beyond
        # "ends with /information", so "nodes/n1/extra/information" still matches
        message = _message(make_message, "nodes/n1/extra/information", {"ip": "10.0.0.1"})
        calls = []
        monkeypatch.setattr(
            mqtt_client.candidate_operations,
            "update_candidate_information",
            lambda client_id, payload: calls.append((client_id, payload)) or {"ok": 1},
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        assert calls == [("n1", {"ip": "10.0.0.1"})]

    def test_topic_prefix_must_be_nodes(self, mqtt_mock, make_message):
        message = _message(make_message, "other/n1/information", {"ip": "10.0.0.1"})
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_not_called()

    def test_net_namespace_topic_is_ignored(self, mqtt_mock, make_message):
        # oakestra-net's topics share the broker but must never be picked up here
        message = _message(make_message, "nodes/n1/net/service/deployed", {"appname": "x"})
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_not_called()


class TestInformationHandler:
    def test_information_drops_none_values_and_updates_candidate(
        self, mqtt_mock, make_message, monkeypatch
    ):
        captured = {}

        def fake_update(client_id, payload):
            captured["client_id"] = client_id
            captured["payload"] = payload
            return {"_id": client_id}

        monkeypatch.setattr(
            mqtt_client.candidate_operations, "update_candidate_information", fake_update
        )
        message = _message(make_message, "nodes/n1/information", {"ip": "10.0.0.1", "port": None})
        mqtt_client.handle_mqtt_message(None, None, message)
        assert captured == {"client_id": "n1", "payload": {"ip": "10.0.0.1"}}

    def test_information_unknown_node_publishes_control_error(
        self, mqtt_mock, make_message, monkeypatch
    ):
        monkeypatch.setattr(
            mqtt_client.candidate_operations, "update_candidate_information", lambda *a: None
        )
        message = _message(make_message, "nodes/n1/information", {"ip": "10.0.0.1"})
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_called_once_with(
            "nodes/n1/control/error",
            json.dumps({"message": "Node not registered to the cluster"}),
        )

    def test_information_known_node_publishes_nothing(self, mqtt_mock, make_message, monkeypatch):
        # an empty dict is "not None", so the known-node branch is taken
        monkeypatch.setattr(
            mqtt_client.candidate_operations, "update_candidate_information", lambda *a: {}
        )
        message = _message(make_message, "nodes/n1/information", {"ip": "10.0.0.1"})
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_not_called()

    def test_information_non_object_payload_raises_attribute_error(self, mqtt_mock, make_message):
        # payload.items() is called unconditionally; a JSON array/scalar has no .items()
        message = _message(make_message, "nodes/n1/information", [1, 2, 3])
        with pytest.raises(AttributeError):
            mqtt_client.handle_mqtt_message(None, None, message)


class TestJobHandler:
    def test_job_status_forwards_all_fields(self, mqtt_mock, make_message, monkeypatch):
        calls = []
        monkeypatch.setattr(
            mqtt_client,
            "update_deployed_instance_worker",
            lambda *a: calls.append(a),
        )
        message = _message(
            make_message,
            "nodes/n1/job",
            {
                "sname": "app.ns.svc.inst",
                "status": "RUNNING",
                "status_detail": "ok",
                "instance": 3,
                "publicip": "10.0.0.9",
            },
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        # status is converted to a Status enum internally, then .value passed through
        assert calls == [("app.ns.svc.inst", 3, "RUNNING", "ok", "10.0.0.9")]

    def test_job_status_defaults_publicip_to_dashes_and_detail_to_none(
        self, mqtt_mock, make_message, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            mqtt_client,
            "update_deployed_instance_worker",
            lambda *a: calls.append(a),
        )
        message = _message(make_message, "nodes/n1/job", {"sname": "a", "status": "CREATED"})
        mqtt_client.handle_mqtt_message(None, None, message)
        assert calls == [("a", None, "CREATED", None, "--")]

    def test_job_status_empty_publicip_is_passed_through(
        self, mqtt_mock, make_message, monkeypatch
    ):
        # "--" is only a default; an explicit empty string is not replaced
        calls = []
        monkeypatch.setattr(
            mqtt_client,
            "update_deployed_instance_worker",
            lambda *a: calls.append(a),
        )
        message = _message(
            make_message, "nodes/n1/job", {"sname": "a", "status": "CREATED", "publicip": ""}
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        assert calls == [("a", None, "CREATED", None, "")]

    def test_job_status_missing_instance_passes_none(self, mqtt_mock, make_message, monkeypatch):
        calls = []
        monkeypatch.setattr(
            mqtt_client,
            "update_deployed_instance_worker",
            lambda *a: calls.append(a),
        )
        message = _message(make_message, "nodes/n1/job", {"sname": "a", "status": "CREATED"})
        mqtt_client.handle_mqtt_message(None, None, message)
        assert calls[0][1] is None

    @pytest.mark.parametrize("bad_status", [None, "", "absent"])
    def test_job_status_missing_status_raises_attribute_error(
        self, mqtt_mock, make_message, bad_status
    ):
        # convert_to_status(None or "") returns None, and .value is then called on
        # it unconditionally - one dropped/blank status field kills MQTT intake.
        payload = {"sname": "a"}
        if bad_status != "absent":
            payload["status"] = bad_status
        message = _message(make_message, "nodes/n1/job", payload)
        with pytest.raises(AttributeError):
            mqtt_client.handle_mqtt_message(None, None, message)

    def test_job_status_unknown_status_raises_value_error(self, mqtt_mock, make_message):
        message = _message(make_message, "nodes/n1/job", {"sname": "a", "status": "NOT_A_STATUS"})
        with pytest.raises(ValueError):
            mqtt_client.handle_mqtt_message(None, None, message)

    def test_job_status_accepts_every_go_status(
        self, mqtt_mock, make_message, monkeypatch, contract
    ):
        monkeypatch.setattr(mqtt_client, "update_deployed_instance_worker", lambda *a: None)
        for status in contract("statuses"):
            message = _message(make_message, "nodes/n1/job", {"sname": "a", "status": status})
            mqtt_client.handle_mqtt_message(None, None, message)  # must not raise


class TestResourcesHandler:
    def test_resources_missing_services_raises_type_error(self, mqtt_mock, make_message):
        message = _message(make_message, "nodes/n1/jobs/resources", {})
        with pytest.raises(TypeError):
            mqtt_client.handle_mqtt_message(None, None, message)

    def test_resources_empty_services_noop(self, mqtt_mock, make_message):
        message = _message(make_message, "nodes/n1/jobs/resources", {"services": []})
        mqtt_client.handle_mqtt_message(None, None, message)  # must not raise
        mqtt_mock.publish.assert_not_called()

    def test_resources_updates_each_service_with_instance_default_zero(
        self, mqtt_mock, make_message, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            mqtt_client, "update_deployed_instance_job", lambda *a: calls.append(a) or {"ok": 1}
        )
        message = _message(
            make_message,
            "nodes/n1/jobs/resources",
            {"services": [{"job_name": "a"}]},
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        assert calls == [("a", 0, {"job_name": "a"}, "n1")]

    def test_resources_stale_service_publishes_control_delete(
        self, mqtt_mock, make_message, monkeypatch
    ):
        monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", lambda *a: None)
        deletes = []
        monkeypatch.setattr(mqtt_client, "mqtt_publish_edge_delete", lambda *a: deletes.append(a))
        message = _message(
            make_message,
            "nodes/n1/jobs/resources",
            {"services": [{"job_name": "a", "instance": 2, "virtualization": "docker"}]},
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        assert deletes == [("n1", "a", 2, "docker")]

    def test_resources_stale_service_without_instance_swallows_type_error(
        self, mqtt_mock, make_message, monkeypatch, caplog
    ):
        # mqtt_publish_edge_delete does int(None) when "instance" is absent; the
        # per-service try/except in the dispatcher swallows that TypeError.
        monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", lambda *a: None)
        message = _message(
            make_message, "nodes/n1/jobs/resources", {"services": [{"job_name": "a"}]}
        )
        with caplog.at_level(logging.ERROR, logger="cluster_manager"):
            mqtt_client.handle_mqtt_message(None, None, message)  # must not raise
        assert "unable to update service resources" in caplog.text

    def test_resources_stale_service_with_null_virtualization_publishes_null_runtime(
        self, mqtt_mock, make_message, monkeypatch
    ):
        monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", lambda *a: None)
        message = _message(
            make_message,
            "nodes/n1/jobs/resources",
            {"services": [{"job_name": "a", "instance": 1, "virtualization": None}]},
        )
        mqtt_client.handle_mqtt_message(None, None, message)
        mqtt_mock.publish.assert_called_once_with(
            "nodes/n1/control/delete",
            json.dumps({"job_name": "a", "virtualization": None, "instance_number": 1}),
        )

    def test_resources_exception_in_one_service_does_not_stop_others(
        self, mqtt_mock, make_message, monkeypatch
    ):
        calls = []

        def fake_update(job_name, instance, service, client_id):
            if job_name == "bad":
                raise KeyError("boom")
            calls.append(job_name)
            return {"ok": 1}

        monkeypatch.setattr(mqtt_client, "update_deployed_instance_job", fake_update)
        message = _message(
            make_message,
            "nodes/n1/jobs/resources",
            {
                "services": [
                    {"job_name": "bad", "instance": 1},
                    {"job_name": "good", "instance": 1},
                ]
            },
        )
        mqtt_client.handle_mqtt_message(None, None, message)  # must not raise
        assert calls == ["good"]


class TestPublishEdgeDeploy:
    def test_publish_edge_deploy_topic_and_payload(self, mqtt_mock):
        job = {"_id": "65d200f3812caeb85e21ee19", "job_name": "x"}
        mqtt_client.mqtt_publish_edge_deploy("node1", job, "2")
        mqtt_mock.publish.assert_called_once_with(
            "nodes/node1/control/deploy",
            json.dumps({"_id": "65d200f3812caeb85e21ee19", "job_name": "x", "instance_number": 2}),
        )

    def test_publish_edge_deploy_mutates_caller_dict(self, mqtt_mock):
        # the caller's dict is mutated in place (instance_number added, _id
        # stringified) - callers must not reuse it expecting the original shape.
        job = {"_id": "abc", "job_name": "x"}
        mqtt_client.mqtt_publish_edge_deploy("node1", job, 5)
        assert job == {"_id": "abc", "job_name": "x", "instance_number": 5}

    def test_publish_edge_deploy_missing_id_becomes_string_none(self, mqtt_mock):
        # str(None) == "None": a job without _id is deployed with a literal "None" id
        job = {"job_name": "x"}
        mqtt_client.mqtt_publish_edge_deploy("node1", job, 1)
        assert job["_id"] == "None"

    def test_publish_edge_deploy_non_numeric_instance_raises_value_error(self, mqtt_mock):
        with pytest.raises(ValueError):
            mqtt_client.mqtt_publish_edge_deploy("node1", {"_id": "a"}, "not-a-number")


class TestPublishEdgeDelete:
    def test_publish_edge_delete_default_runtime(self, mqtt_mock):
        mqtt_client.mqtt_publish_edge_delete("node1", "x", 1)
        mqtt_mock.publish.assert_called_once_with(
            "nodes/node1/control/delete",
            json.dumps({"job_name": "x", "virtualization": "docker", "instance_number": 1}),
        )

    def test_publish_edge_delete_explicit_runtime(self, mqtt_mock):
        mqtt_client.mqtt_publish_edge_delete("node1", "x", 1, "containerd")
        mqtt_mock.publish.assert_called_once_with(
            "nodes/node1/control/delete",
            json.dumps({"job_name": "x", "virtualization": "containerd", "instance_number": 1}),
        )

    def test_publish_edge_delete_instance_number_is_cast_to_int(self, mqtt_mock):
        mqtt_client.mqtt_publish_edge_delete("node1", "x", "3")
        mqtt_mock.publish.assert_called_once_with(
            "nodes/node1/control/delete",
            json.dumps({"job_name": "x", "virtualization": "docker", "instance_number": 3}),
        )

    def test_publish_edge_delete_none_instance_raises_type_error(self, mqtt_mock):
        with pytest.raises(TypeError):
            mqtt_client.mqtt_publish_edge_delete("node1", "x", None)
