import pytest
from clients import workerlink
from oakestra_messaging.mqtt import MqttBus


@pytest.fixture
def broker_env(monkeypatch):
    monkeypatch.setenv("MQTT_BROKER_URL", "broker.local")
    monkeypatch.setenv("MQTT_BROKER_PORT", "10003")
    monkeypatch.delenv("MQTT_CERT", raising=False)
    monkeypatch.delenv("MESSAGING_BACKEND", raising=False)
    yield monkeypatch


def test_bus_from_env_builds_mqtt_bus_with_cluster_qos(broker_env):
    bus = workerlink.bus_from_env()

    assert isinstance(bus, MqttBus)
    assert bus.qos == 0
    assert bus.host == "broker.local"
    assert bus.port == 10003


def test_bus_from_env_strips_ipv6_brackets(broker_env):
    broker_env.setenv("MQTT_BROKER_URL", "[::1]")
    bus = workerlink.bus_from_env()
    assert bus.host == "::1"


def test_bus_from_env_without_mqtt_cert_has_no_tls(broker_env):
    bus = workerlink.bus_from_env()
    assert bus.tls is None


def test_bus_from_env_with_mqtt_cert_builds_cluster_paths(broker_env, monkeypatch):
    # tls_set() is called eagerly by MqttBus.__init__; avoid touching the
    # filesystem for cert files that don't exist in the test environment.
    import oakestra_messaging.mqtt as mqtt_module

    monkeypatch.setattr(mqtt_module.paho.Client, "tls_set", lambda self, **kwargs: None)
    broker_env.setenv("MQTT_CERT", "/certs")
    broker_env.setenv("CLUSTER_KEYFILE_PASSWORD", "s3cret")

    bus = workerlink.bus_from_env()

    assert bus.tls.ca_certs == "/certs/ca.crt"
    assert bus.tls.certfile == "/certs/cluster.crt"
    assert bus.tls.keyfile == "/certs/cluster.key"
    assert bus.tls.keyfile_password == "s3cret"


def test_bus_from_env_unsupported_backend_raises_value_error(broker_env):
    broker_env.setenv("MESSAGING_BACKEND", "nats")
    with pytest.raises(ValueError, match="unsupported MESSAGING_BACKEND 'nats'"):
        workerlink.bus_from_env()
