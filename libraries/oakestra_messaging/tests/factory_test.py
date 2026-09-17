import pytest
from oakestra_messaging import MqttBus, TlsConfig, from_env, mqtt_tls_from_env


def test_from_env_default_backend_builds_mqtt_bus():
    env = {"MQTT_BROKER_URL": "broker.local", "MQTT_BROKER_PORT": "10003"}
    bus = from_env(qos=0, env=env)

    assert isinstance(bus, MqttBus)
    assert bus.host == "broker.local"
    assert bus.port == 10003
    assert bus.qos == 0


def test_from_env_explicit_mqtt_backend():
    env = {"MESSAGING_BACKEND": "mqtt", "MQTT_BROKER_URL": "h", "MQTT_BROKER_PORT": "1"}
    bus = from_env(qos=1, env=env)

    assert isinstance(bus, MqttBus)


def test_from_env_unsupported_backend_raises_value_error():
    env = {"MESSAGING_BACKEND": "nats", "MQTT_BROKER_URL": "h", "MQTT_BROKER_PORT": "1"}

    with pytest.raises(ValueError, match="unsupported MESSAGING_BACKEND 'nats'"):
        from_env(qos=0, env=env)


def test_from_env_strips_ipv6_brackets():
    env = {"MQTT_BROKER_URL": "[::1]", "MQTT_BROKER_PORT": "10003"}
    bus = from_env(qos=0, env=env)

    assert bus.host == "::1"


def test_from_env_strip_only_touches_ends():
    # str.strip("[]") only peels matching chars off the ends, so a bracket
    # stranded in the middle of the value survives untouched.
    env = {"MQTT_BROKER_URL": "[a]b[c]", "MQTT_BROKER_PORT": "10003"}
    bus = from_env(qos=0, env=env)

    assert bus.host == "a]b[c"


def test_from_env_missing_broker_url_raises_key_error():
    env = {"MQTT_BROKER_PORT": "10003"}

    with pytest.raises(KeyError):
        from_env(qos=0, env=env)


def test_from_env_non_numeric_port_raises_value_error():
    env = {"MQTT_BROKER_URL": "h", "MQTT_BROKER_PORT": "not-a-port"}

    with pytest.raises(ValueError):
        from_env(qos=0, env=env)


def test_from_env_passes_through_client_id_and_qos():
    env = {"MQTT_BROKER_URL": "h", "MQTT_BROKER_PORT": "1"}
    bus = from_env(qos=1, client_id="cid", env=env)

    assert bus.client_id == "cid"
    assert bus.qos == 1


def test_from_env_passes_through_tls(monkeypatch):
    # tls_set() is called eagerly in MqttBus.__init__, so avoid touching the
    # filesystem here and just verify the TlsConfig instance is forwarded
    import oakestra_messaging.mqtt as mqtt_module

    monkeypatch.setattr(mqtt_module.paho.Client, "tls_set", lambda self, **kwargs: None)
    tls = TlsConfig("ca", "cert", "key")
    env = {"MQTT_BROKER_URL": "h", "MQTT_BROKER_PORT": "1"}

    bus = from_env(qos=0, tls=tls, env=env)

    assert bus.tls is tls


def test_mqtt_tls_from_env_none_without_mqtt_cert():
    assert mqtt_tls_from_env("cluster", "CLUSTER_KEYFILE_PASSWORD", env={}) is None


def test_mqtt_tls_from_env_builds_paths_for_cluster_manager():
    env = {"MQTT_CERT": "/certs", "CLUSTER_KEYFILE_PASSWORD": "s3cret"}
    tls = mqtt_tls_from_env("cluster", "CLUSTER_KEYFILE_PASSWORD", env=env)

    assert tls == TlsConfig("/certs/ca.crt", "/certs/cluster.crt", "/certs/cluster.key", "s3cret")


def test_mqtt_tls_from_env_builds_paths_for_cluster_service_manager():
    env = {"MQTT_CERT": "/certs", "CLUSTER_SERVICE_KEYFILE_PASSWORD": "pw"}
    tls = mqtt_tls_from_env("cluster_net", "CLUSTER_SERVICE_KEYFILE_PASSWORD", env=env)

    assert tls == TlsConfig(
        "/certs/ca.crt", "/certs/cluster_net.crt", "/certs/cluster_net.key", "pw"
    )


def test_mqtt_tls_from_env_password_is_none_when_unset():
    env = {"MQTT_CERT": "/certs"}
    tls = mqtt_tls_from_env("cluster", "CLUSTER_KEYFILE_PASSWORD", env=env)

    assert tls.keyfile_password is None
