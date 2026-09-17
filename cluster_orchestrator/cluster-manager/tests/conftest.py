import json
import os

# Must run before any production module is imported: several modules build
# request URLs at import time (e.g. config.py concatenates os.environ.get(...)
# results directly), so a missing var blows up on import, not on use.
os.environ.setdefault("MQTT_BROKER_URL", "127.0.0.1")
os.environ.setdefault("MQTT_BROKER_PORT", "1883")
os.environ.setdefault("CLUSTER_SCHEDULER_URL", "localhost")
os.environ.setdefault("CLUSTER_SCHEDULER_PORT", "10105")
os.environ.setdefault("RESOURCE_ABSTRACTOR_URL", "localhost")
os.environ.setdefault("RESOURCE_ABSTRACTOR_PORT", "11012")
os.environ.setdefault("CLUSTER_SERVICE_MANAGER_ADDR", "localhost")
os.environ.setdefault("CLUSTER_SERVICE_MANAGER_PORT", "10110")
os.environ.setdefault("SYSTEM_MANAGER_URL", "localhost")
os.environ.setdefault("SYSTEM_MANAGER_PORT", "10000")
os.environ.setdefault("SYSTEM_MANAGER_GRPC_PORT", "50052")

from pathlib import Path
from unittest.mock import MagicMock

import clients.mqtt_client
import pytest
from paho.mqtt.client import MQTTMessage

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "testdata" / "mqtt_contract"


@pytest.fixture
def mqtt_mock(monkeypatch):
    """Replace the module-level paho client with a MagicMock so publish/subscribe
    calls can be asserted without a broker."""
    mock = MagicMock()
    monkeypatch.setattr(clients.mqtt_client, "mqtt", mock)
    return mock


@pytest.fixture
def make_message():
    """Build a real MQTTMessage, matching what paho hands to on_message."""

    def _make(topic, payload):
        message = MQTTMessage(topic=topic.encode())
        if isinstance(payload, (bytes, bytearray)):
            message.payload = bytes(payload)
        else:
            message.payload = payload.encode()
        return message

    return _make


@pytest.fixture
def contract():
    """Load a golden MQTT payload fixture by file stem (no .json suffix)."""

    def _load(name):
        with open(CONTRACT_DIR / f"{name}.json") as f:
            return json.load(f)

    return _load
