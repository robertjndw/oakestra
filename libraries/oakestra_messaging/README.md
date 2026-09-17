# oakestra_messaging

Technology-neutral pub/sub interface used by Oakestra's cluster tier to talk
to workers. It hides the transport (MQTT today) behind a small `MessageBus`
API so a future NATS adapter needs no change in the callers.

## API

```python
from oakestra_messaging import Message, MessageBus, MqttBus, InMemoryBus, TlsConfig, from_env

bus: MessageBus = from_env(qos=0)
bus.subscribe("nodes/+/information", handler)  # allowed before connect()
bus.connect()  # blocks until the broker acks every pattern
bus.publish("nodes/n1/control/deploy", json.dumps(payload))
bus.close()
```

- `Message(topic: str, payload: bytes)` - frozen dataclass, what handlers receive.
- `Handler = Callable[[Message], None]`.
- `MessageBus` (abstract): `connect(timeout=10.0)`, `subscribe(pattern, handler)`,
  `unsubscribe(pattern)`, `publish(topic, payload)`, `close()`.
  - `connect()` blocks until connected and every registered pattern has been
    acked by the broker, or raises `TimeoutError`. `timeout=None` returns
    immediately without waiting.
  - `subscribe()` can be called before `connect()`; every registered pattern
    is re-issued on every (re)connect, and a subscribe issued after
    `connect()` is sent immediately.
  - `publish()` accepts `bytes` or `str` (encoded as utf-8) and never sets
    the retain flag.
- `MqttBus(host, port, *, client_id="", keepalive=5, qos=0, tls=None, ...)` -
  the paho-mqtt backed implementation. Works with paho-mqtt 1.6.1 and 2.x.
- `InMemoryBus()` - for tests. `publish()` records to `.published` and loops
  the message back to matching local subscribers, like a real broker would.
  `deliver(topic, payload)` injects an inbound message from "outside".
- `Dispatcher(logger)` - the shared routing engine both buses use: a handler
  that raises is logged (`logger.exception`) and never stops the other
  matching handlers or the network thread.
- `matches(pattern, topic)` - the topic-matching predicate, exposed for
  callers that want to test their own routing without a bus.

## Topic syntax

Canonical topics are `/`-separated, e.g. `nodes/<node-id>/information`.
Patterns may use:

- `+` - exactly one segment.
- `#` - the rest of the topic, only as the last segment of the pattern. It
  also matches zero remaining segments, so `nodes/n1/#` matches `nodes/n1`.

`+` never partially matches within a segment: `nodes/+/information` does not
match `nodes/n1/extra/information`, and `nodes/+/net/subnet` does not match
`nodes/n1/net/subnetwork/result`.

### NATS caveat

A future NATS adapter maps `/` to `.`, `+` to `*`, and `#` to `>`. Job names
(`app.ns.svc.inst`) and service IPs already contain `.`, which becomes the
NATS token separator. Exact-match subjects such as
`jobs/<job>/updates_available` survive that mapping unchanged, but a `+`
wildcard must never be placed over a segment that may itself contain a `.` -
it would then match more or fewer NATS tokens than the single MQTT segment it
was meant to stand for. Node IDs are ObjectId hex (no dots), so `nodes/+/...`
patterns are safe under both backends.

## Configuration

- `MESSAGING_BACKEND` - selects the backend. Defaults to `mqtt`, the only
  currently valid value; anything else raises `ValueError` at startup.
- `MQTT_BROKER_URL`, `MQTT_BROKER_PORT` - broker address. The URL is stripped
  of surrounding `[]` (for bracketed IPv6 literals).
- `MQTT_CERT` - directory holding `ca.crt` and a `<basename>.crt` /
  `<basename>.key` pair. When unset, `mqtt_tls_from_env()` returns `None` and
  the bus connects without TLS.

```python
from oakestra_messaging import from_env, mqtt_tls_from_env

bus = from_env(qos=0, tls=mqtt_tls_from_env("cluster", "CLUSTER_KEYFILE_PASSWORD"))
```

## Running the tests

```bash
pip install -e . pytest
pytest                              # unit tests only, integration tests are skipped

# broker-backed integration tests need a live broker:
docker run -d --rm --name oakestra-test-mqtt -p 11883:10003 \
  -v "$PWD/../../cluster_orchestrator/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  eclipse-mosquitto:2.0
OAKESTRA_TEST_MQTT_ADDR=127.0.0.1:11883 pytest -m integration -v
docker stop oakestra-test-mqtt
```

CI (`.github/workflows/messaging_library_tests.yml`) runs the full suite,
including integration tests against a Mosquitto container, under both
paho-mqtt 1.6.1 and 2.1.0.
