from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes


Handler = Callable[[Message], None]


class MessageBus(ABC):
    """Technology-neutral pub/sub used by both cluster tiers to talk to workers.

    Implementations hide the transport (MQTT today, NATS later) behind the same
    canonical topic syntax; see topics.py for the pattern grammar.
    """

    @abstractmethod
    def connect(self, timeout: float | None = 10.0) -> None:
        """Block until connected and every registered pattern has been acked.

        Raises TimeoutError if that has not happened within `timeout` seconds.
        `timeout=None` means: do not wait, return immediately.
        """

    @abstractmethod
    def subscribe(self, pattern: str, handler: Handler) -> None:
        """Register a handler for a topic pattern.

        Allowed before connect(); the subscription is re-issued on every
        (re)connect so it survives broker restarts.
        """

    @abstractmethod
    def unsubscribe(self, pattern: str) -> None: ...

    @abstractmethod
    def publish(self, topic: str, payload: bytes | str) -> None:
        """Publish payload to topic. str payloads are encoded as utf-8. Never retained."""

    @abstractmethod
    def close(self) -> None: ...
