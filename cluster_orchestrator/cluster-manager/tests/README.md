# cluster_manager tests

Characterization tests for `clients/mqtt_client.py` and its callers
(`blueprints/service_blueprints.py`, `clients/job_management.py`): they lock in the
CURRENT behavior of the MQTT link between cluster_manager and NodeEngine,
quirks included, as a safety net for the eventual MQTT -> NATS migration.

## Setup

```bash
cd cluster_orchestrator/cluster-manager
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -r requirements-test.txt ../../libraries/oakestra_utils_library ../../libraries/resource_abstractor_client
```

## Running

```bash
pytest                        # unit tests only; integration tests are skipped
pytest -m integration -v      # integration tests only, needs a broker (see below)
pytest                        # everything, once the env var below is exported
```

Unit tests mock the paho client (`clients.mqtt_client.mqtt`) and never touch
the network. Integration tests spin up a second, real paho client ("the
peer", standing in for a NodeEngine worker) against an actual Mosquitto
broker and exercise `mqtt_init`/`handle_mqtt_message` for real.

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

- `test_malformed_payload_then_valid_message_is_still_processed` is an
  `xfail(strict=True)`: paho-mqtt 1.6.1 runs with `suppress_exceptions=False`,
  and `handle_mqtt_message` calls `json.loads()` before any topic check, so a
  single malformed payload raises out of `on_message` and kills the
  `loop_start()` background thread. cluster_manager stops processing MQTT
  entirely afterwards. The test is expected to keep failing (xfail) until
  that thread is made resilient; if it ever starts passing, `strict=True`
  turns that into a hard failure so the fix doesn't go unnoticed.
- `POST /api/result/deploy` 500s whenever the scheduler reports a scheduling
  failure (no `candidate_id` in the payload): the log statement in
  `blueprints/service_blueprints.py` string-concatenates that `None` before
  the `candidate_id is None` branch is reached. See
  `tests/mqtt_callers_test.py::TestSchedulingResultDeploy::test_missing_candidate_id_crashes_before_any_status_update`.
- See `testdata/mqtt_contract/README.md` at the repo root for the wire-level
  quirks (untagged fields on `node_information`, `control/error` having no
  consumer, etc).
