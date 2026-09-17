# cluster_manager tests

Characterization tests for `clients/workerlink.py` and its callers
(`blueprints/service_blueprints.py`, `clients/job_management.py`): they lock in the
CURRENT behavior of the link between cluster_manager and NodeEngine, quirks
included, as a safety net for the eventual MQTT -> NATS migration.

## Setup

```bash
cd cluster_orchestrator/cluster-manager
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -r requirements-test.txt ../../libraries/oakestra_utils_library ../../libraries/resource_abstractor_client ../../libraries/oakestra_messaging
```

## Running

```bash
pytest                        # unit tests only; integration tests are skipped
pytest -m integration -v      # integration tests only, needs a broker (see below)
pytest                        # everything, once the env var below is exported
```

Unit tests exercise `clients/workerlink.py` against an `InMemoryBus` (the
`bus` fixture in `conftest.py`) and never touch the network. paho-mqtt is no
longer imported by production code at all; the `oakestra_messaging` library
hides it behind `MessageBus`, and the only place paho still shows up in this
suite is the integration tests, which spin up a second, real paho client
("the peer", standing in for a NodeEngine worker) against an actual
Mosquitto broker and exercise `workerlink.start`/`bus.connect` for real.

## Broker for integration tests

Integration tests read the broker address from `OAKESTRA_TEST_MQTT_ADDR`
(`host:port`) and are skipped (not failed) when it is unset:

```bash
docker run -d --rm --name oakestra-test-mqtt -p 11883:10003 \
  -v "$(pwd)/../mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  eclipse-mosquitto:2.0

export OAKESTRA_TEST_MQTT_ADDR=127.0.0.1:11883
pytest -m integration -v

docker stop oakestra-test-mqtt
```

## Step 3 preview (NATS)

Once MQTT is replaced by NATS running with its MQTT-compatibility mode
enabled, these integration tests only need `OAKESTRA_TEST_MQTT_ADDR`
re-pointed at the `nats-server` container's MQTT port. Nothing else in this
suite (fixtures, dispatch, or assertions) is Mosquitto-specific.

## Known quirks these tests document

- `POST /api/result/deploy` 500s whenever the scheduler reports a scheduling
  failure (no `candidate_id` in the payload): the log statement in
  `blueprints/service_blueprints.py` string-concatenates that `None` before
  the `candidate_id is None` branch is reached. See
  `tests/workerlink_callers_test.py::TestSchedulingResultDeploy::test_missing_candidate_id_crashes_before_any_status_update`.
- See `testdata/mqtt_contract/README.md` at the repo root for the wire-level
  quirks (untagged fields on `node_information`, `control/error` having no
  consumer, etc).
