import os

from .bus import MessageBus
from .mqtt import MqttBus, TlsConfig

BACKENDS = ("mqtt",)


def mqtt_tls_from_env(cert_basename: str, password_env: str, env=os.environ) -> TlsConfig | None:
    """Build a TlsConfig from the MQTT_CERT directory convention, or None if unset."""
    if "MQTT_CERT" not in env:
        return None
    cert_dir = env["MQTT_CERT"]
    return TlsConfig(
        ca_certs=f"{cert_dir}/ca.crt",
        certfile=f"{cert_dir}/{cert_basename}.crt",
        keyfile=f"{cert_dir}/{cert_basename}.key",
        keyfile_password=env.get(password_env),
    )


def from_env(
    *, qos: int, tls: TlsConfig | None = None, client_id: str = "", logger=None, env=os.environ
) -> MessageBus:
    """Build the configured MessageBus. MESSAGING_BACKEND defaults to "mqtt";
    any other value fails fast so a typo in the deployment config is caught at
    startup rather than as a silent no-op bus later.
    """
    backend = env.get("MESSAGING_BACKEND", "mqtt")
    if backend not in BACKENDS:
        raise ValueError(f"unsupported MESSAGING_BACKEND '{backend}'; valid: {', '.join(BACKENDS)}")

    host = env["MQTT_BROKER_URL"].strip("[]")
    port = int(env["MQTT_BROKER_PORT"])
    return MqttBus(host, port, client_id=client_id, qos=qos, tls=tls, logger=logger)
