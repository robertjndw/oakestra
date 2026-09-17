import json
import logging

import clients.workerlink as workerlink
import pytest
from oakestra_messaging import Message


class TestStart:
    def test_start_subscribes_exactly_three_patterns(self, bus):
        assert sorted(bus.subscriptions) == sorted(
            ["nodes/+/information", "nodes/+/job", "nodes/+/jobs/resources"]
        )


class TestDispatch:
    def test_malformed_payload_is_logged_and_next_message_is_still_processed(
        self, bus, monkeypatch, caplog
    ):
        calls = []
        monkeypatch.setattr(
            workerlink.candidate_operations,
            "update_candidate_information",
            lambda client_id, payload: calls.append((client_id, payload)) or {"ok": 1},
        )
        with caplog.at_level(logging.ERROR):
            delivered = bus.deliver("nodes/n1/information", "not-json")
        assert delivered == 1
        assert caplog.text  # the JSONDecodeError was logged, not raised

        bus.deliver("nodes/n1/information", json.dumps({"ip": "10.0.0.1"}))
        assert calls == [("n1", {"ip": "10.0.0.1"})]

    def test_unknown_topic_valid_json_is_ignored(self, bus):
        delivered = bus.deliver("nodes/n1/unknown", "{}")
        assert delivered == 0
        assert bus.published == []

    def test_extra_segment_information_topic_is_not_delivered(self, bus, monkeypatch):
        # nodes/+/information matches exactly three segments; a fourth segment
        # no longer matches now that routing is pattern-based, not substring-based
        calls = []
        monkeypatch.setattr(
            workerlink.candidate_operations,
            "update_candidate_information",
            lambda client_id, payload: calls.append((client_id, payload)) or {"ok": 1},
        )
        delivered = bus.deliver("nodes/n1/extra/information", json.dumps({"ip": "10.0.0.1"}))
        assert delivered == 0
        assert calls == []

    def test_topic_prefix_must_be_nodes(self, bus):
        delivered = bus.deliver("other/n1/information", json.dumps({"ip": "10.0.0.1"}))
        assert delivered == 0
        assert bus.published == []

    def test_net_namespace_topic_is_ignored(self, bus):
        # oakestra-net's topics share the broker but must never be picked up here
        delivered = bus.deliver("nodes/n1/net/service/deployed", json.dumps({"appname": "x"}))
        assert delivered == 0
        assert bus.published == []


class TestInformationHandler:
    def test_information_drops_none_values_and_updates_candidate(self, bus, monkeypatch):
        captured = {}

        def fake_update(client_id, payload):
            captured["client_id"] = client_id
            captured["payload"] = payload
            return {"_id": client_id}

        monkeypatch.setattr(
            workerlink.candidate_operations, "update_candidate_information", fake_update
        )
        bus.deliver("nodes/n1/information", json.dumps({"ip": "10.0.0.1", "port": None}))
        assert captured == {"client_id": "n1", "payload": {"ip": "10.0.0.1"}}

    def test_information_unknown_node_publishes_control_error(self, bus, monkeypatch):
        monkeypatch.setattr(
            workerlink.candidate_operations, "update_candidate_information", lambda *a: None
        )
        bus.deliver("nodes/n1/information", json.dumps({"ip": "10.0.0.1"}))
        assert bus.published[-1] == Message(
            "nodes/n1/control/error",
            json.dumps({"message": "Node not registered to the cluster"}).encode(),
        )

    def test_information_known_node_publishes_nothing(self, bus, monkeypatch):
        # an empty dict is "not None", so the known-node branch is taken
        monkeypatch.setattr(
            workerlink.candidate_operations, "update_candidate_information", lambda *a: {}
        )
        bus.deliver("nodes/n1/information", json.dumps({"ip": "10.0.0.1"}))
        assert bus.published == []

    def test_information_non_object_payload_is_logged_and_next_message_processed(
        self, bus, monkeypatch, caplog
    ):
        # payload.items() is called unconditionally; a JSON array/scalar has no .items()
        with caplog.at_level(logging.ERROR):
            delivered = bus.deliver("nodes/n1/information", json.dumps([1, 2, 3]))
        assert delivered == 1
        assert caplog.text

        calls = []
        monkeypatch.setattr(
            workerlink.candidate_operations,
            "update_candidate_information",
            lambda client_id, payload: calls.append((client_id, payload)) or {"ok": 1},
        )
        bus.deliver("nodes/n1/information", json.dumps({"ip": "10.0.0.1"}))
        assert calls == [("n1", {"ip": "10.0.0.1"})]


class TestJobHandler:
    def test_job_status_forwards_all_fields(self, bus, monkeypatch):
        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        payload = {
            "sname": "app.ns.svc.inst",
            "status": "RUNNING",
            "status_detail": "ok",
            "instance": 3,
            "publicip": "10.0.0.9",
        }
        bus.deliver("nodes/n1/job", json.dumps(payload))
        # status is converted to a Status enum internally, then .value passed through
        assert calls == [("app.ns.svc.inst", 3, "RUNNING", "ok", "10.0.0.9")]

    def test_job_status_defaults_publicip_to_dashes_and_detail_to_none(self, bus, monkeypatch):
        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": "CREATED"}))
        assert calls == [("a", None, "CREATED", None, "--")]

    def test_job_status_empty_publicip_is_passed_through(self, bus, monkeypatch):
        # "--" is only a default; an explicit empty string is not replaced
        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": "CREATED", "publicip": ""}))
        assert calls == [("a", None, "CREATED", None, "")]

    def test_job_status_missing_instance_passes_none(self, bus, monkeypatch):
        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": "CREATED"}))
        assert calls[0][1] is None

    @pytest.mark.parametrize("bad_status", [None, "", "absent"])
    def test_job_status_missing_status_is_logged_and_next_message_processed(
        self, bus, monkeypatch, caplog, bad_status
    ):
        # convert_to_status(None or "") returns None, and .value is called on
        # it unconditionally; the dispatcher's per-handler try/except keeps
        # that from blocking the next message.
        payload = {"sname": "a"}
        if bad_status != "absent":
            payload["status"] = bad_status
        with caplog.at_level(logging.ERROR):
            delivered = bus.deliver("nodes/n1/job", json.dumps(payload))
        assert delivered == 1
        assert caplog.text

        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": "CREATED"}))
        assert calls == [("a", None, "CREATED", None, "--")]

    def test_job_status_unknown_status_is_logged_and_next_message_processed(
        self, bus, monkeypatch, caplog
    ):
        with caplog.at_level(logging.ERROR):
            delivered = bus.deliver(
                "nodes/n1/job", json.dumps({"sname": "a", "status": "NOT_A_STATUS"})
            )
        assert delivered == 1
        assert caplog.text

        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_worker", lambda *a: calls.append(a)
        )
        bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": "CREATED"}))
        assert calls == [("a", None, "CREATED", None, "--")]

    def test_job_status_accepts_every_go_status(self, bus, monkeypatch, contract):
        monkeypatch.setattr(workerlink, "update_deployed_instance_worker", lambda *a: None)
        for status in contract("statuses"):
            bus.deliver("nodes/n1/job", json.dumps({"sname": "a", "status": status}))  # no raise


class TestResourcesHandler:
    def test_resources_missing_services_is_logged_and_next_message_processed(
        self, bus, monkeypatch, caplog
    ):
        with caplog.at_level(logging.ERROR):
            delivered = bus.deliver("nodes/n1/jobs/resources", json.dumps({}))
        assert delivered == 1
        assert caplog.text

        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_job", lambda *a: calls.append(a) or {"ok": 1}
        )
        bus.deliver("nodes/n1/jobs/resources", json.dumps({"services": [{"job_name": "a"}]}))
        assert calls == [("a", 0, {"job_name": "a"}, "n1")]

    def test_resources_empty_services_noop(self, bus):
        bus.deliver("nodes/n1/jobs/resources", json.dumps({"services": []}))
        assert bus.published == []

    def test_resources_updates_each_service_with_instance_default_zero(self, bus, monkeypatch):
        calls = []
        monkeypatch.setattr(
            workerlink, "update_deployed_instance_job", lambda *a: calls.append(a) or {"ok": 1}
        )
        bus.deliver("nodes/n1/jobs/resources", json.dumps({"services": [{"job_name": "a"}]}))
        assert calls == [("a", 0, {"job_name": "a"}, "n1")]

    def test_resources_stale_service_publishes_control_delete(self, bus, monkeypatch):
        monkeypatch.setattr(workerlink, "update_deployed_instance_job", lambda *a: None)
        deletes = []
        monkeypatch.setattr(workerlink, "publish_delete", lambda *a: deletes.append(a))
        bus.deliver(
            "nodes/n1/jobs/resources",
            json.dumps(
                {"services": [{"job_name": "a", "instance": 2, "virtualization": "docker"}]}
            ),
        )
        assert deletes == [("n1", "a", 2, "docker")]

    def test_resources_stale_service_without_instance_swallows_type_error(
        self, bus, caplog, monkeypatch
    ):
        # publish_delete does int(None) when "instance" is absent; the
        # per-service try/except in the handler swallows that TypeError.
        monkeypatch.setattr(workerlink, "update_deployed_instance_job", lambda *a: None)
        with caplog.at_level(logging.ERROR, logger="cluster_manager"):
            bus.deliver(
                "nodes/n1/jobs/resources", json.dumps({"services": [{"job_name": "a"}]})
            )  # must not raise
        assert "unable to update service resources" in caplog.text

    def test_resources_stale_service_with_null_virtualization_publishes_null_runtime(
        self, bus, monkeypatch
    ):
        monkeypatch.setattr(workerlink, "update_deployed_instance_job", lambda *a: None)
        bus.deliver(
            "nodes/n1/jobs/resources",
            json.dumps({"services": [{"job_name": "a", "instance": 1, "virtualization": None}]}),
        )
        assert bus.published[-1] == Message(
            "nodes/n1/control/delete",
            json.dumps({"job_name": "a", "virtualization": None, "instance_number": 1}).encode(),
        )

    def test_resources_exception_in_one_service_does_not_stop_others(self, bus, monkeypatch):
        calls = []

        def fake_update(job_name, instance, service, client_id):
            if job_name == "bad":
                raise KeyError("boom")
            calls.append(job_name)
            return {"ok": 1}

        monkeypatch.setattr(workerlink, "update_deployed_instance_job", fake_update)
        bus.deliver(
            "nodes/n1/jobs/resources",
            json.dumps(
                {
                    "services": [
                        {"job_name": "bad", "instance": 1},
                        {"job_name": "good", "instance": 1},
                    ]
                }
            ),
        )  # must not raise
        assert calls == ["good"]


class TestPublishDeploy:
    def test_publish_deploy_topic_and_payload(self, bus):
        job = {"_id": "65d200f3812caeb85e21ee19", "job_name": "x"}
        workerlink.publish_deploy("node1", job, "2")
        assert bus.published[-1] == Message(
            "nodes/node1/control/deploy",
            json.dumps(
                {"_id": "65d200f3812caeb85e21ee19", "job_name": "x", "instance_number": 2}
            ).encode(),
        )

    def test_publish_deploy_mutates_caller_dict(self, bus):
        # the caller's dict is mutated in place (instance_number added, _id
        # stringified) - callers must not reuse it expecting the original shape.
        job = {"_id": "abc", "job_name": "x"}
        workerlink.publish_deploy("node1", job, 5)
        assert job == {"_id": "abc", "job_name": "x", "instance_number": 5}

    def test_publish_deploy_missing_id_becomes_string_none(self, bus):
        # str(None) == "None": a job without _id is deployed with a literal "None" id
        job = {"job_name": "x"}
        workerlink.publish_deploy("node1", job, 1)
        assert job["_id"] == "None"

    def test_publish_deploy_non_numeric_instance_raises_value_error(self, bus):
        with pytest.raises(ValueError):
            workerlink.publish_deploy("node1", {"_id": "a"}, "not-a-number")


class TestPublishDelete:
    def test_publish_delete_default_runtime(self, bus):
        workerlink.publish_delete("node1", "x", 1)
        assert bus.published[-1] == Message(
            "nodes/node1/control/delete",
            json.dumps(
                {"job_name": "x", "virtualization": "docker", "instance_number": 1}
            ).encode(),
        )

    def test_publish_delete_explicit_runtime(self, bus):
        workerlink.publish_delete("node1", "x", 1, "containerd")
        assert bus.published[-1] == Message(
            "nodes/node1/control/delete",
            json.dumps(
                {"job_name": "x", "virtualization": "containerd", "instance_number": 1}
            ).encode(),
        )

    def test_publish_delete_instance_number_is_cast_to_int(self, bus):
        workerlink.publish_delete("node1", "x", "3")
        assert bus.published[-1] == Message(
            "nodes/node1/control/delete",
            json.dumps(
                {"job_name": "x", "virtualization": "docker", "instance_number": 3}
            ).encode(),
        )

    def test_publish_delete_none_instance_raises_type_error(self, bus):
        with pytest.raises(TypeError):
            workerlink.publish_delete("node1", "x", None)
