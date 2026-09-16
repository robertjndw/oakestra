from unittest.mock import MagicMock, patch

import clients.mqtt_client as mqtt_client
import pytest


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MQTT_BROKER_URL", "broker.local")
    monkeypatch.setenv("MQTT_BROKER_PORT", "10003")
    monkeypatch.delenv("MQTT_CERT", raising=False)
    yield monkeypatch


@patch("clients.mqtt_client.paho_mqtt.Client")
class TestMqttInit:
    def test_init_wires_callbacks_and_options(self, client_cls, env):
        instance = client_cls.return_value
        mqtt_client.mqtt_init(flask_app=MagicMock())

        client_cls.assert_called_once_with()
        assert instance.on_connect is mqtt_client.handle_connect
        assert instance.on_message is mqtt_client.handle_mqtt_message
        instance.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=120)
        instance.max_queued_messages_set.assert_called_once_with(1000)
        instance.connect.assert_called_once_with("broker.local", 10003, keepalive=5)
        instance.loop_start.assert_called_once()
        assert mqtt_client.mqtt is instance

    def test_init_strips_ipv6_brackets(self, client_cls, env):
        env.setenv("MQTT_BROKER_URL", "[::1]")
        instance = client_cls.return_value
        mqtt_client.mqtt_init(None)
        instance.connect.assert_called_once_with("::1", 10003, keepalive=5)

    def test_init_strip_only_touches_ends(self, client_cls, env):
        # str.strip("[]") only peels matching chars off the ends, so a bracket
        # stranded in the middle of the value survives untouched.
        env.setenv("MQTT_BROKER_URL", "[a]b[c]")
        instance = client_cls.return_value
        mqtt_client.mqtt_init(None)
        instance.connect.assert_called_once_with("a]b[c", 10003, keepalive=5)

    def test_init_without_mqtt_cert_does_not_call_tls_set(self, client_cls, env):
        instance = client_cls.return_value
        mqtt_client.mqtt_init(None)
        instance.tls_set.assert_not_called()

    def test_init_with_mqtt_cert_calls_tls_set_with_paths(self, client_cls, env):
        env.setenv("MQTT_CERT", "/certs")
        env.setenv("CLUSTER_KEYFILE_PASSWORD", "s3cret")
        instance = client_cls.return_value
        mqtt_client.mqtt_init(None)
        instance.tls_set.assert_called_once_with(
            ca_certs="/certs/ca.crt",
            certfile="/certs/cluster.crt",
            keyfile="/certs/cluster.key",
            keyfile_password="s3cret",
        )

    def test_init_with_mqtt_cert_and_no_password_passes_none(self, client_cls, env):
        env.setenv("MQTT_CERT", "/certs")
        env.delenv("CLUSTER_KEYFILE_PASSWORD", raising=False)
        instance = client_cls.return_value
        mqtt_client.mqtt_init(None)
        assert instance.tls_set.call_args.kwargs["keyfile_password"] is None

    def test_init_swallows_file_not_found_from_tls_set(self, client_cls, env):
        # a missing cert file is logged, not fatal - mqtt_init still connects
        # and starts the loop with no TLS configured.
        env.setenv("MQTT_CERT", "/certs")
        instance = client_cls.return_value
        instance.tls_set.side_effect = FileNotFoundError("no such file")
        mqtt_client.mqtt_init(None)
        instance.connect.assert_called_once()
        instance.loop_start.assert_called_once()

    def test_init_other_tls_errors_propagate(self, client_cls, env):
        env.setenv("MQTT_CERT", "/certs")
        instance = client_cls.return_value
        instance.tls_set.side_effect = ValueError("bad cert")
        with pytest.raises(ValueError):
            mqtt_client.mqtt_init(None)
        instance.connect.assert_not_called()

    def test_init_missing_broker_url_raises_attribute_error(self, client_cls, env):
        env.delenv("MQTT_BROKER_URL", raising=False)
        with pytest.raises(AttributeError):
            mqtt_client.mqtt_init(None)

    def test_init_non_numeric_port_raises_value_error(self, client_cls, env):
        env.setenv("MQTT_BROKER_PORT", "not-a-port")
        with pytest.raises(ValueError):
            mqtt_client.mqtt_init(None)

    def test_init_second_call_replaces_global_without_stopping_first(self, client_cls, env):
        # mqtt_init has no idempotency guard: calling it twice orphans the first
        # client (still connected, looping in its own thread) with nothing left
        # referencing it to call loop_stop()/disconnect() on.
        first, second = MagicMock(), MagicMock()
        client_cls.side_effect = [first, second]

        mqtt_client.mqtt_init(None)
        assert mqtt_client.mqtt is first

        mqtt_client.mqtt_init(None)
        assert mqtt_client.mqtt is second
        first.loop_stop.assert_not_called()
        first.disconnect.assert_not_called()
