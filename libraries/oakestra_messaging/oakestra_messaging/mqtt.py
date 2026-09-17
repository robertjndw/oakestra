import logging
import threading
from dataclasses import dataclass

import paho.mqtt.client as paho

try:
    # paho >=2.0 requires picking a callback API version explicitly, or it
    # emits a DeprecationWarning and falls back to the old (v1) signatures.
    from paho.mqtt.enums import CallbackAPIVersion

    _HAS_CALLBACK_API_V2 = True
except ImportError:
    _HAS_CALLBACK_API_V2 = False

from .bus import Handler, Message, MessageBus
from .dispatch import Dispatcher


@dataclass(frozen=True)
class TlsConfig:
    ca_certs: str
    certfile: str
    keyfile: str
    keyfile_password: str | None = None


def _build_client(client_id, client_factory):
    if client_factory is not None:
        return client_factory(client_id)
    if _HAS_CALLBACK_API_V2:
        return paho.Client(CallbackAPIVersion.VERSION2, client_id=client_id)
    return paho.Client(client_id=client_id)


class MqttBus(MessageBus):
    """MessageBus backed by paho-mqtt. Works with paho 1.6.1 and 2.x."""

    def __init__(
        self,
        host,
        port,
        *,
        client_id="",
        keepalive=5,
        qos=0,
        tls: TlsConfig | None = None,
        reconnect_delay=(1, 120),
        max_queued_messages=1000,
        logger=None,
        client_factory=None,
    ):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._keepalive = keepalive
        self._qos = qos
        self._tls = tls
        self._logger = logger or logging.getLogger(__name__)
        self._dispatcher = Dispatcher(self._logger)

        self._started = False
        self._connected_event = threading.Event()
        self._pending_mids: set[int] = set()
        self._mids_lock = threading.Lock()

        self._client = _build_client(client_id, client_factory)
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

        self._client.reconnect_delay_set(min_delay=reconnect_delay[0], max_delay=reconnect_delay[1])
        self._client.max_queued_messages_set(max_queued_messages)

        if tls is not None:
            # a missing cert file must abort startup here, not fail silently
            # into a plaintext connection later
            self._client.tls_set(
                ca_certs=tls.ca_certs,
                certfile=tls.certfile,
                keyfile=tls.keyfile,
                keyfile_password=tls.keyfile_password,
            )

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    @property
    def client_id(self):
        return self._client_id

    @property
    def keepalive(self):
        return self._keepalive

    @property
    def qos(self):
        return self._qos

    @property
    def tls(self):
        return self._tls

    def connect(self, timeout: float | None = 10.0) -> None:
        self._connected_event.clear()
        with self._mids_lock:
            self._pending_mids.clear()
        self._client.connect(self._host, self._port, keepalive=self._keepalive)
        self._client.loop_start()
        self._started = True
        if timeout is None:
            return
        if not self._connected_event.wait(timeout):
            raise TimeoutError(
                f"MQTT connect to {self._host}:{self._port} timed out after {timeout}s "
                "waiting for the broker to ack all subscriptions"
            )

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._dispatcher.add(pattern, handler)
        if self._started:
            self._client.subscribe(pattern, qos=self._qos)

    def unsubscribe(self, pattern: str) -> None:
        self._dispatcher.remove(pattern)
        if self._started:
            self._client.unsubscribe(pattern)

    def publish(self, topic: str, payload: bytes | str) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        self._client.publish(topic, data, qos=self._qos)

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self._logger.info("MQTT - connected to %s:%s", self._host, self._port)
        with self._mids_lock:
            self._pending_mids.clear()
            for pattern in self._dispatcher.patterns:
                _, mid = self._client.subscribe(pattern, qos=self._qos)
                self._pending_mids.add(mid)
            if not self._pending_mids:
                self._connected_event.set()

    def _on_subscribe(self, client, userdata, mid, *args):
        with self._mids_lock:
            self._pending_mids.discard(mid)
            if not self._pending_mids:
                self._connected_event.set()

    def _on_message(self, client, userdata, msg):
        called = self._dispatcher.dispatch(Message(msg.topic, msg.payload))
        if called == 0:
            self._logger.debug("no handler registered for topic %r", msg.topic)
