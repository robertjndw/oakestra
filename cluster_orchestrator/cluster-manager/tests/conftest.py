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

import clients.workerlink as workerlink
import pytest
from oakestra_messaging import InMemoryBus

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "testdata" / "mqtt_contract"


@pytest.fixture
def bus():
    """An in-memory bus wired up exactly like production, without a broker."""
    test_bus = InMemoryBus()
    workerlink.start(test_bus)
    yield test_bus
    workerlink._bus = None


@pytest.fixture
def contract():
    """Load a golden MQTT payload fixture by file stem (no .json suffix)."""

    def _load(name):
        with open(CONTRACT_DIR / f"{name}.json") as f:
            return json.load(f)

    return _load
