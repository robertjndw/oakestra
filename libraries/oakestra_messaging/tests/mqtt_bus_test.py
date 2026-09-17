import logging
import warnings

import pytest
from oakestra_messaging import MqttBus, TlsConfig


def _ack_all_on_loop_start(fake_paho):
    """Wire the fake client so calling connect() looks like a broker that
    accepts the connection and acks every subscription immediately."""

    def fake_loop_start(*args, **kwargs):
        fake_paho.loop_start_calls.append((args, kwargs))
        fake_paho.fire_connect()
        for _, _, mid in fake_paho.subscribe_calls:
            fake_paho.fire_suback(mid)

    fake_paho.loop_start = fake_loop_start


def test_init_sets_reconnect_delay_and_queue_size(fake_paho):
    MqttBus("h", 1, client_factory=lambda cid: fake_paho)

    assert fake_paho.reconnect_delay_set_calls == [((), {"min_delay": 1, "max_delay": 120})]
    assert fake_paho.max_queued_messages_set_calls == [((1000,), {})]


def test_init_honors_custom_reconnect_delay_and_queue_size(fake_paho):
    MqttBus(
        "h",
        1,
        reconnect_delay=(2, 60),
        max_queued_messages=50,
        client_factory=lambda cid: fake_paho,
    )

    assert fake_paho.reconnect_delay_set_calls == [((), {"min_delay": 2, "max_delay": 60})]
    assert fake_paho.max_queued_messages_set_calls == [((50,), {})]


def test_connect_passes_host_port_and_keepalive(fake_paho):
    bus = MqttBus("broker.local", 10003, keepalive=7, client_factory=lambda cid: fake_paho)
    bus.connect(timeout=None)

    assert fake_paho.connect_calls == [(("broker.local", 10003), {"keepalive": 7})]


def test_read_only_attributes(fake_paho):
    tls = TlsConfig("ca", "cert", "key")
    bus = MqttBus(
        "h", 123, client_id="cid", keepalive=9, qos=1, tls=tls, client_factory=lambda cid: fake_paho
    )

    assert bus.host == "h"
    assert bus.port == 123
    assert bus.client_id == "cid"
    assert bus.keepalive == 9
    assert bus.qos == 1
    assert bus.tls is tls


def test_tls_set_called_with_kwargs_in_init(fake_paho):
    tls = TlsConfig(
        ca_certs="/certs/ca.crt",
        certfile="/certs/cluster.crt",
        keyfile="/certs/cluster.key",
        keyfile_password="s3cret",
    )
    MqttBus("h", 1, tls=tls, client_factory=lambda cid: fake_paho)

    assert fake_paho.tls_set_calls == [
        (
            (),
            {
                "ca_certs": "/certs/ca.crt",
                "certfile": "/certs/cluster.crt",
                "keyfile": "/certs/cluster.key",
                "keyfile_password": "s3cret",
            },
        )
    ]


def test_no_tls_means_tls_set_is_never_called(fake_paho):
    MqttBus("h", 1, client_factory=lambda cid: fake_paho)

    assert fake_paho.tls_set_calls == []


def test_tls_set_errors_propagate(fake_paho):
    def boom(**kwargs):
        raise FileNotFoundError("no such cert")

    fake_paho.tls_set = boom
    tls = TlsConfig("ca", "cert", "key")

    with pytest.raises(FileNotFoundError):
        MqttBus("h", 1, tls=tls, client_factory=lambda cid: fake_paho)


def test_connect_blocks_until_every_pattern_is_acked(make_bus, fake_paho):
    _ack_all_on_loop_start(fake_paho)
    bus = make_bus(qos=1)
    bus.subscribe("nodes/+/information", lambda m: None)
    bus.subscribe("nodes/+/job", lambda m: None)

    bus.connect(timeout=1)

    subs = [(args, kwargs) for args, kwargs, _ in fake_paho.subscribe_calls]
    assert subs == [
        (("nodes/+/information",), {"qos": 1}),
        (("nodes/+/job",), {"qos": 1}),
    ]


def test_connect_with_no_patterns_still_waits_for_on_connect(make_bus, fake_paho):
    _ack_all_on_loop_start(fake_paho)
    bus = make_bus()

    bus.connect(timeout=1)  # must not raise: on_connect ran, nothing to ack


def test_connect_raises_timeout_error_when_broker_never_acks(make_bus):
    bus = make_bus()
    bus.subscribe("a", lambda m: None)

    with pytest.raises(TimeoutError):
        bus.connect(timeout=0.05)


def test_connect_timeout_none_returns_without_waiting(make_bus, fake_paho):
    bus = make_bus()
    bus.subscribe("a", lambda m: None)

    bus.connect(timeout=None)  # no ack ever fired; must still return

    assert fake_paho.connect_calls
    assert fake_paho.loop_start_calls


def test_on_connect_resubscribes_all_patterns_at_bus_qos(make_bus, fake_paho):
    bus = make_bus(qos=1)
    bus.subscribe("a", lambda m: None)
    bus.subscribe("b", lambda m: None)

    fake_paho.fire_connect()

    subs = [(args, kwargs) for args, kwargs, _ in fake_paho.subscribe_calls]
    assert subs == [(("a",), {"qos": 1}), (("b",), {"qos": 1})]


def test_on_connect_resubscribes_again_on_every_reconnect(make_bus, fake_paho):
    bus = make_bus()
    bus.subscribe("a", lambda m: None)

    fake_paho.fire_connect()
    fake_paho.fire_connect()

    assert len(fake_paho.subscribe_calls) == 2


def test_subscribe_before_connect_does_not_touch_the_client(fake_paho):
    bus = MqttBus("h", 1, client_factory=lambda cid: fake_paho)
    bus.subscribe("a", lambda m: None)

    assert fake_paho.subscribe_calls == []


def test_subscribe_after_connect_issues_immediately(make_bus, fake_paho):
    _ack_all_on_loop_start(fake_paho)
    bus = make_bus(qos=1)
    bus.connect(timeout=1)

    bus.subscribe("new/pattern", lambda m: None)

    args, kwargs, _ = fake_paho.subscribe_calls[-1]
    assert args == ("new/pattern",)
    assert kwargs == {"qos": 1}


def test_unsubscribe_before_connect_does_not_touch_the_client(fake_paho):
    bus = MqttBus("h", 1, client_factory=lambda cid: fake_paho)
    bus.subscribe("a", lambda m: None)
    bus.unsubscribe("a")

    assert fake_paho.unsubscribe_calls == []


def test_unsubscribe_after_connect_calls_the_client(make_bus, fake_paho):
    _ack_all_on_loop_start(fake_paho)
    bus = make_bus()
    bus.connect(timeout=1)
    bus.subscribe("a", lambda m: None)

    bus.unsubscribe("a")

    assert fake_paho.unsubscribe_calls == [(("a",), {})]


def test_publish_bytes_uses_bus_qos_and_no_retain(make_bus, fake_paho):
    bus = make_bus(qos=1)

    bus.publish("nodes/n1/control/deploy", b"payload")

    assert fake_paho.publish_calls == [(("nodes/n1/control/deploy", b"payload"), {"qos": 1})]


def test_publish_str_payload_is_utf8_encoded(make_bus, fake_paho):
    bus = make_bus()

    bus.publish("nodes/n1/control/deploy", "hello")

    assert fake_paho.publish_calls == [(("nodes/n1/control/deploy", b"hello"), {"qos": 0})]


def test_publish_never_passes_retain(make_bus, fake_paho):
    bus = make_bus()

    bus.publish("t", b"x")

    args, kwargs = fake_paho.publish_calls[0]
    assert len(args) == 2
    assert "retain" not in kwargs


def test_on_message_dispatches_to_matching_handler(make_bus, fake_paho):
    bus = make_bus()
    seen = []
    bus.subscribe("nodes/+/information", lambda m: seen.append(m.payload))

    fake_paho.fire_message("nodes/n1/information", b"{}")

    assert seen == [b"{}"]


def test_on_message_handler_exception_is_logged_and_next_message_still_dispatched(
    make_bus, fake_paho, caplog
):
    logger = logging.getLogger("mqtt-bus-test-exc")
    bus = make_bus(logger=logger)
    seen = []

    def broken(message):
        raise ValueError("boom")

    bus.subscribe("a", broken)
    bus.subscribe("b", lambda m: seen.append(m.topic))

    with caplog.at_level(logging.ERROR, logger="mqtt-bus-test-exc"):
        fake_paho.fire_message("a", b"")
        fake_paho.fire_message("b", b"")

    assert seen == ["b"]
    assert any(record.exc_info for record in caplog.records)


def test_on_message_unmatched_topic_is_logged_at_debug(make_bus, fake_paho, caplog):
    logger = logging.getLogger("mqtt-bus-test-debug")
    make_bus(logger=logger)  # wires fake_paho.on_message; the bus itself isn't needed after that

    with caplog.at_level(logging.DEBUG, logger="mqtt-bus-test-debug"):
        fake_paho.fire_message("unhandled/topic", b"")

    assert any("no handler" in record.getMessage() for record in caplog.records)


def test_close_calls_loop_stop_before_disconnect(make_bus, fake_paho):
    order = []
    fake_paho.loop_stop = lambda *a, **k: order.append("loop_stop")
    fake_paho.disconnect = lambda *a, **k: order.append("disconnect")
    bus = make_bus()

    bus.close()

    assert order == ["loop_stop", "disconnect"]


def test_shim_builds_real_client_without_deprecation_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        MqttBus("localhost", 1883, client_id="shim-test")


def test_shim_uses_callback_api_version_matching_installed_paho(monkeypatch):
    import oakestra_messaging.mqtt as mqtt_module

    captured = {}

    class FakeRealClient:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        def on_connect(self):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def max_queued_messages_set(self, *args):
            pass

    monkeypatch.setattr(mqtt_module.paho, "Client", FakeRealClient)

    MqttBus("h", 1, client_id="my-id")

    assert captured["kwargs"] == {"client_id": "my-id"}
    if mqtt_module._HAS_CALLBACK_API_V2:
        assert captured["args"] == (mqtt_module.CallbackAPIVersion.VERSION2,)
    else:
        assert captured["args"] == ()
