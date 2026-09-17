import os
from types import SimpleNamespace

import pytest
from oakestra_messaging.mqtt import MqttBus


class FakePahoClient:
    """Stand-in for paho.mqtt.client.Client.

    Records every call it receives (args and kwargs, verbatim) so tests can
    assert exactly what MqttBus sent, and exposes fire_*() helpers to drive
    the callbacks MqttBus registered, the way the paho network thread would.
    """

    def __init__(self):
        self.on_connect = None
        self.on_subscribe = None
        self.on_message = None

        self.connect_calls = []
        self.loop_start_calls = []
        self.loop_stop_calls = []
        self.disconnect_calls = []
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        self.publish_calls = []
        self.tls_set_calls = []
        self.reconnect_delay_set_calls = []
        self.max_queued_messages_set_calls = []

        self._next_mid = 0

    def connect(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))

    def loop_start(self, *args, **kwargs):
        self.loop_start_calls.append((args, kwargs))

    def loop_stop(self, *args, **kwargs):
        self.loop_stop_calls.append((args, kwargs))

    def disconnect(self, *args, **kwargs):
        self.disconnect_calls.append((args, kwargs))

    def subscribe(self, *args, **kwargs):
        self._next_mid += 1
        mid = self._next_mid
        self.subscribe_calls.append((args, kwargs, mid))
        return (0, mid)

    def unsubscribe(self, *args, **kwargs):
        self.unsubscribe_calls.append((args, kwargs))
        return (0, 0)

    def publish(self, *args, **kwargs):
        self.publish_calls.append((args, kwargs))
        return (0, 0)

    def tls_set(self, *args, **kwargs):
        self.tls_set_calls.append((args, kwargs))

    def reconnect_delay_set(self, *args, **kwargs):
        self.reconnect_delay_set_calls.append((args, kwargs))

    def max_queued_messages_set(self, *args, **kwargs):
        self.max_queued_messages_set_calls.append((args, kwargs))

    def fire_connect(self, rc=0, flags=None):
        self.on_connect(self, None, flags if flags is not None else {}, rc)

    def fire_suback(self, mid, granted_qos=None):
        self.on_subscribe(self, None, mid, granted_qos if granted_qos is not None else [0])

    def fire_message(self, topic, payload):
        data = payload.encode() if isinstance(payload, str) else payload
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=data))


@pytest.fixture
def fake_paho():
    return FakePahoClient()


@pytest.fixture
def make_bus(fake_paho):
    """Build an MqttBus wired to the fake_paho client via the client_factory seam."""

    def _make(host="broker.local", port=10003, **kwargs):
        kwargs.setdefault("client_factory", lambda client_id: fake_paho)
        return MqttBus(host, port, **kwargs)

    return _make


@pytest.fixture(scope="session")
def broker_addr():
    addr = os.environ.get("OAKESTRA_TEST_MQTT_ADDR")
    if not addr:
        pytest.skip("OAKESTRA_TEST_MQTT_ADDR is not set; skipping broker-backed tests")
    host, port = addr.rsplit(":", 1)
    return host, int(port)
