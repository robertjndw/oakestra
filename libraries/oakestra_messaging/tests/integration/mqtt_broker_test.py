import contextlib
import json
import queue
import threading
import time
import uuid

import paho.mqtt.client as paho_mqtt
import pytest
from oakestra_messaging import MqttBus

pytestmark = pytest.mark.integration


class _Peer:
    """A bare paho client standing in for the "other side" of the wire."""

    def __init__(self, client):
        self._client = client
        self._queue = queue.Queue()

    def _on_message(self, client, userdata, message):
        self._queue.put(message)

    def expect(self, timeout=5):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no message received within {timeout}s") from None

    def expect_none(self, timeout=0.5):
        try:
            message = self._queue.get(timeout=timeout)
        except queue.Empty:
            return
        raise AssertionError(f"unexpected message on {message.topic!r}")

    def publish(self, topic, payload, qos=1):
        self._client.publish(topic, payload, qos=qos)

    def subscribe(self, pattern, qos=1):
        subscribed = threading.Event()

        def on_subscribe(client, userdata, mid, granted_qos):
            subscribed.set()

        self._client.on_subscribe = on_subscribe
        self._client.subscribe(pattern, qos=qos)
        if not subscribed.wait(timeout=5):
            raise AssertionError("peer did not subscribe in time")


@pytest.fixture
def peer(broker_addr):
    host, port = broker_addr
    client = paho_mqtt.Client()
    wrapper = _Peer(client)
    client.on_message = wrapper._on_message

    connected = threading.Event()
    client.on_connect = lambda c, u, f, rc: connected.set()
    client.connect(host, port, keepalive=5)
    client.loop_start()
    if not connected.wait(timeout=5):
        raise AssertionError("peer did not connect in time")

    yield wrapper

    # teardown best-effort: the broker connection may already be gone if the
    # test itself closed it
    with contextlib.suppress(Exception):
        client.loop_stop()
        client.disconnect()


@pytest.fixture
def topic():
    # unique per test so retained state or timing from another test/run
    # can never leak into this one
    return f"it/{uuid.uuid4().hex[:8]}"


def _bus(broker_addr, **kwargs):
    host, port = broker_addr
    return MqttBus(host, port, **kwargs)


def _wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_round_trip_with_plus_pattern(broker_addr, peer, topic):
    bus = _bus(broker_addr, qos=1)
    received = []
    bus.subscribe(f"{topic}/+/information", lambda m: received.append(m))
    bus.connect(timeout=5)

    peer.publish(f"{topic}/n1/information", json.dumps({"a": 1}), qos=1)
    assert _wait_until(lambda: received)
    bus.close()

    assert received[0].topic == f"{topic}/n1/information"
    assert json.loads(received[0].payload) == {"a": 1}


def test_publish_qos_is_observed_on_the_wire(broker_addr, peer, topic):
    for bus_qos in (0, 1):
        sub_topic = f"{topic}/{bus_qos}/control/deploy"
        peer.subscribe(sub_topic, qos=1)
        bus = _bus(broker_addr, qos=bus_qos)
        bus.connect(timeout=5)

        bus.publish(sub_topic, b"payload")
        message = peer.expect()
        bus.close()

        assert message.qos == bus_qos


def test_publish_never_retains(broker_addr, peer, topic):
    pub_topic = f"{topic}/retain-check"
    bus = _bus(broker_addr, qos=1)
    bus.connect(timeout=5)
    bus.publish(pub_topic, b"x")
    bus.close()

    # a subscriber joining after the publish must not receive anything retained
    peer.subscribe(pub_topic, qos=1)
    peer.expect_none(timeout=1)


def test_handler_exception_does_not_stop_later_messages(broker_addr, peer, topic):
    bus = _bus(broker_addr, qos=1)
    seen = []

    def broken(message):
        raise ValueError("boom")

    bus.subscribe(f"{topic}/broken", broken)
    bus.subscribe(f"{topic}/ok", lambda m: seen.append(m.topic))
    bus.connect(timeout=5)

    peer.publish(f"{topic}/broken", b"x", qos=1)
    peer.publish(f"{topic}/ok", b"y", qos=1)
    assert _wait_until(lambda: seen)
    bus.close()

    assert seen == [f"{topic}/ok"]


def test_unsubscribe_stops_delivery(broker_addr, peer, topic):
    sub_topic = f"{topic}/x"
    bus = _bus(broker_addr, qos=1)
    received = []
    bus.subscribe(sub_topic, lambda m: received.append(m))
    bus.connect(timeout=5)

    bus.unsubscribe(sub_topic)
    peer.publish(sub_topic, b"gone", qos=1)
    time.sleep(1)
    bus.close()

    assert received == []


def test_publish_right_after_connect_returns_is_received(broker_addr, peer, topic):
    pub_topic = f"{topic}/immediate"
    peer.subscribe(pub_topic, qos=1)
    bus = _bus(broker_addr, qos=1)
    bus.connect(timeout=5)
    bus.publish(pub_topic, b"go")

    message = peer.expect()
    bus.close()

    assert message.payload == b"go"


def test_reconnect_resubscribes(broker_addr, peer, topic):
    pub_topic = f"{topic}/reconnect"
    bus = _bus(broker_addr, qos=1)
    received = []
    bus.subscribe(pub_topic, lambda m: received.append(m))
    bus.connect(timeout=5)

    bus._client.reconnect()
    # reconnect() is synchronous but the still-running network thread needs a
    # moment to settle on the new socket before the broker treats us as
    # connected again and CONNACK triggers on_connect's resubscribe
    assert _wait_until(bus._client.is_connected, timeout=10)

    peer.publish(pub_topic, b"after-reconnect", qos=1)
    assert _wait_until(lambda: received)
    bus.close()

    assert received[-1].payload == b"after-reconnect"
