# MQTT contract fixtures (oakestra)

Golden payloads for the MQTT link between `cluster_manager` (CM, Python) and
`NodeEngine` (NE, Go). Both test suites load these files and compare them
semantically (parsed JSON, not bytes). Change a fixture only together with both
suites: `cluster_orchestrator/cluster-manager/tests/` and `go_node_engine/mqtt/`.

All topics are `nodes/<nodeID>/...`, where `nodeID` is the worker's cluster
assigned `_id` from `POST /api/node/register`.

| Fixture | Topic | Producer | Consumer | Producer QoS |
|---|---|---|---|---|
| `node_information.json` | `nodes/<id>/information` | NE `ReportNodeInformation` | CM -> `update_candidate_information` | 1 |
| `job_status.json` | `nodes/<id>/job` | NE `ReportServiceStatus` | CM -> `update_deployed_instance_worker` | 1 |
| `jobs_resources.json` | `nodes/<id>/jobs/resources` | NE `ReportServiceResources` | CM -> `update_deployed_instance_job` | 1 |
| `control_deploy.json` | `nodes/<id>/control/deploy` | CM `mqtt_publish_edge_deploy` | NE `deployHandler` | 0 |
| `control_delete.json` | `nodes/<id>/control/delete` | CM `mqtt_publish_edge_delete` | NE `deleteHandler` | 0 |
| `control_error.json` | `nodes/<id>/control/error` | CM (unknown node) | nobody | 0 |
| `statuses.json` | payload field `status` | NE `model.SERVICE_*` constants | CM `convert_to_status` | |

Notes pinned by the tests:

- `control/error` has no consumer. NE only subscribes to `control/deploy` and
  `control/delete`.
- `node_information.json` carries five untagged Go fields (`Overlay`,
  `OverlaySocket`, `LogDirectory`, `NetManagerPort`, `ClusterAddress`) that leak
  onto the wire because `model.Node` has no JSON tags for them. CM forwards
  them to the resource abstractor verbatim.
- `control_deploy.json` is the raw Mongo job document plus `instance_number`.
  It contains keys NE does not know (`microserviceID`, `instance_list`), which
  Go silently drops.
- CM publishes at QoS 0, NE publishes at QoS 1. Neither side retains.
- The `nodes/<id>/net/#` and `jobs/<job>/updates_available` namespaces belong
  to oakestra-net and are covered by that repository's fixtures.
