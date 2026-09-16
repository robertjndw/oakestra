from unittest.mock import patch

import blueprints.service_blueprints as service_blueprints
import clients.job_management as job_management
from flask import Flask


def _job(instance_list, **extra):
    return {"_id": "job1", "job_name": "app.ns.svc.inst", "instance_list": instance_list, **extra}


class TestDeleteJobInstance:
    def test_publishes_for_instance_with_worker_id(self):
        job = _job(
            [
                {"instance_number": 1, "worker_id": "nodeA"},
                {"instance_number": 2, "worker_id": "nodeB"},
            ],
            virtualization="containerd",
        )
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance") as del_inst,
            patch.object(job_management.job_operations, "delete_job") as del_job,
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            job_management.delete_job_instance("job1", 1)

        publish.assert_called_once_with("nodeA", "app.ns.svc.inst", 1, "containerd")
        del_inst.assert_called_once_with("job1", 1)
        del_job.assert_not_called()

    def test_uses_job_virtualization_default_docker(self):
        job = _job([{"instance_number": 1, "worker_id": "nodeA"}])
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance"),
            patch.object(job_management.job_operations, "delete_job"),
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            job_management.delete_job_instance("job1", 1)

        publish.assert_called_once_with("nodeA", "app.ns.svc.inst", 1, "docker")

    def test_skips_publish_without_worker_id_but_still_erases(self):
        job = _job([{"instance_number": 2}])
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance") as del_inst,
            patch.object(job_management.job_operations, "delete_job"),
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            job_management.delete_job_instance("job1", 2)

        publish.assert_not_called()
        del_inst.assert_called_once_with("job1", 2)

    def test_minus_one_publishes_for_all_and_deletes_job_returning_empty_dict(self):
        job = _job(
            [
                {"instance_number": 1, "worker_id": "nodeA"},
                {"instance_number": 2, "worker_id": "nodeB"},
            ]
        )
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance") as del_inst,
            patch.object(job_management.job_operations, "delete_job") as del_job,
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            result = job_management.delete_job_instance("job1", -1)

        assert publish.call_count == 2
        assert del_inst.call_count == 2
        del_job.assert_called_once_with("job1")
        # once every instance is erased the function returns {} rather than
        # re-fetching the (now deleted) job
        assert result == {}

    def test_erase_false_publishes_without_db_delete(self):
        job = _job([{"instance_number": 1, "worker_id": "nodeA"}])
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance") as del_inst,
            patch.object(job_management.job_operations, "delete_job") as del_job,
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            job_management.delete_job_instance("job1", 1, erase=False)

        publish.assert_called_once()
        del_inst.assert_not_called()
        del_job.assert_not_called()

    def test_non_matching_instance_publishes_nothing(self):
        job = _job([{"instance_number": 1, "worker_id": "nodeA"}])
        with (
            patch.object(job_management.job_operations, "get_job_by_id", return_value=job),
            patch.object(job_management.job_operations, "delete_job_instance") as del_inst,
            patch.object(job_management.job_operations, "delete_job") as del_job,
            patch("clients.mqtt_client.mqtt_publish_edge_delete") as publish,
        ):
            job_management.delete_job_instance("job1", 99)

        publish.assert_not_called()
        del_inst.assert_not_called()
        del_job.assert_not_called()


class TestSchedulingResultDeploy:
    """POST /api/result/deploy, as sent by scheduler/requests/manager/managerRequests.go."""

    def setup_method(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(service_blueprints.schedulingblp)
        self.client = self.app.test_client()

    def test_publishes_with_string_instance_and_node_scheduled_status(self):
        job = {"_id": "job1", "job_name": "app.ns.svc.inst"}
        with (
            patch.object(service_blueprints.job_management, "update_instance_node") as uin,
            patch.object(service_blueprints.job_management, "update_status") as ust,
            patch.object(service_blueprints.job_operations, "get_job_by_id", return_value=job),
            patch.object(service_blueprints, "network_notify_deployment") as notify,
            patch.object(service_blueprints, "mqtt_publish_edge_deploy") as deploy,
        ):
            response = self.client.post(
                "/api/result/deploy", json={"job_id": "job1/2", "candidate_id": "node1"}
            )

        assert response.status_code == 200
        uin.assert_called_once_with("job1", 2, "node1")
        ust.assert_called_once_with("job1", 2, "NODE_SCHEDULED")
        notify.assert_called_once_with("job1", job)
        # instance_number reaches mqtt_publish_edge_deploy as the raw string
        # split out of "job_id", never cast to int
        deploy.assert_called_once_with("node1", job, "2")

    def test_missing_candidate_id_crashes_before_any_status_update(self):
        # data.get("candidate_id") is None whenever the scheduler reports a
        # failure (scheduler/requests/manager/managerRequests.go's
        # deploymentFailedRequest has no candidate_id field at all). The
        # logging call two lines below string-concatenates that None *before*
        # the `if node_id is None` branch is reached, so the "scheduling
        # failed" path 500s instead of ever updating the job status.
        with (
            patch.object(service_blueprints.job_operations, "update_job_status") as ujs,
            patch.object(service_blueprints.job_management, "update_instance_node") as uin,
            patch.object(service_blueprints, "mqtt_publish_edge_deploy") as deploy,
        ):
            response = self.client.post(
                "/api/result/deploy",
                json={"job_id": "job1/2", "status": "NO_WORKER_CAPACITY"},
            )

        assert response.status_code == 500
        ujs.assert_not_called()
        uin.assert_not_called()
        deploy.assert_not_called()

    def test_job_gone_returns_job_not_found_without_publishing(self):
        with (
            patch.object(service_blueprints.job_management, "update_instance_node"),
            patch.object(service_blueprints.job_management, "update_status"),
            patch.object(service_blueprints.job_operations, "get_job_by_id", return_value=None),
            patch.object(service_blueprints, "network_notify_deployment") as notify,
            patch.object(service_blueprints, "mqtt_publish_edge_deploy") as deploy,
        ):
            response = self.client.post(
                "/api/result/deploy", json={"job_id": "job1/2", "candidate_id": "node1"}
            )

        assert response.status_code == 200
        assert response.get_json() == {"status": "job_not_found"}
        notify.assert_not_called()
        deploy.assert_not_called()
