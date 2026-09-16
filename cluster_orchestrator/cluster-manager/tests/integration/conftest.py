import json
import os
import queue
import threading
import time
import uuid

import clients.mqtt_client as mqtt_client
import paho.mqtt.client as paho_mqtt
import pytest


@pytest.fixture(scope="session")
def broker_addr():
    addr = os.environ.get("OAKESTRA_TEST_MQTT_ADDR")
    if not addr:
        pytest.skip("OAKESTRA_TEST_MQTT_ADDR is not set; skipping broker-backed tests")
    host, port = addr.rsplit(":", 1)
    return host, int(port)


@pytest.fixture
def node_id():
    # fresh per test so tests never see another test's retained state or
    # in-flight messages on the broker
    return f"it-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cm_client(broker_addr, monkeypatch):
    host, port = broker_addr
    monkeypatch.setenv("MQTT_BROKER_URL", host)
    monkeypatch.setenv("MQTT_BROKER_PORT", str(port))
    monkeypatch.delenv("MQTT_CERT", raising=False)

    # handle_connect() fires the three subscribe() calls itself; hook
    # on_subscribe from inside it (same thread, before any SUBACK can arrive)
    # so the test can block until the broker has actually acknowledged all
    # three subscriptions instead of racing a fixed sleep.
    subscribed = threading.Event()
    original_handle_connect = mqtt_client.handle_connect

    def wrapped_handle_connect(client, userdata, flags, rc):
        acked = []

        def on_subscribe(client, userdata, mid, granted_qos):
            acked.append(mid)
            if len(acked) >= 3:
                subscribed.set()

        client.on_subscribe = on_subscribe
        original_handle_connect(client, userdata, flags, rc)

    monkeypatch.setattr(mqtt_client, "handle_connect", wrapped_handle_connect)

    mqtt_client.mqtt_init(None)
    client = mqtt_client.mqtt
    if not subscribed.wait(timeout=5):
        raise AssertionError("cluster_manager did not subscribe to all three topics in time")

    yield client

    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass
    mqtt_client.mqtt = None


class Peer:
    """A bare paho client standing in for a worker's NodeEngine."""

    def __init__(self, client):
        self._client = client
        self._queue = queue.Queue()
        # the peer subscribes to nodes/<id>/#, so the broker also echoes back whatever
        # the peer itself publishes there. Drop those echoes by exact (topic, payload)
        # match; CM never publishes the same topic and payload the peer just sent.
        self._own_publishes = set()
        self._lock = threading.Lock()

    def _on_message(self, client, userdata, message):
        key = (message.topic, bytes(message.payload))
        with self._lock:
            if key in self._own_publishes:
                self._own_publishes.discard(key)
                return
        self._queue.put(message)

    def expect(self, topic, timeout=5):
        """Block for a message on exactly `topic`, ignoring anything else."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no message on {topic!r} within {timeout}s")
            try:
                message = self._queue.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError(f"no message on {topic!r} within {timeout}s") from None
            if message.topic == topic:
                return message

    def expect_none(self, timeout=0.5):
        try:
            message = self._queue.get(timeout=timeout)
        except queue.Empty:
            return
        raise AssertionError(f"unexpected message on {message.topic!r}: {message.payload!r}")

    def publish(self, topic, obj, qos=1):
        payload = json.dumps(obj)
        with self._lock:
            self._own_publishes.add((topic, payload.encode()))
        self._client.publish(topic, payload, qos=qos)

    def publish_raw(self, topic, payload, qos=1):
        raw = payload if isinstance(payload, bytes) else payload.encode()
        with self._lock:
            self._own_publishes.add((topic, raw))
        self._client.publish(topic, payload, qos=qos)


@pytest.fixture
def peer(broker_addr, node_id):
    host, port = broker_addr
    client = paho_mqtt.Client()
    wrapper = Peer(client)
    client.on_message = wrapper._on_message

    subscribed = threading.Event()

    def on_connect(client, userdata, flags, rc):
        def on_subscribe(client, userdata, mid, granted_qos):
            subscribed.set()

        client.on_subscribe = on_subscribe
        client.subscribe(f"nodes/{node_id}/#", qos=1)

    client.on_connect = on_connect
    client.connect(host, port, keepalive=5)
    client.loop_start()
    if not subscribed.wait(timeout=5):
        raise AssertionError("peer did not subscribe in time")

    yield wrapper

    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass
