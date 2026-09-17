from .bus import Handler, Message, MessageBus
from .dispatch import Dispatcher


class InMemoryBus(MessageBus):
    """In-process bus for unit tests. Loops publishes back to local subscribers
    the way a real broker would, so callers can be tested without paho at all.
    """

    def __init__(self, logger=None):
        self._dispatcher = Dispatcher(logger)
        self.published: list[Message] = []
        self._connected = False

    def connect(self, timeout: float | None = 10.0) -> None:
        self._connected = True

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._dispatcher.add(pattern, handler)

    def unsubscribe(self, pattern: str) -> None:
        self._dispatcher.remove(pattern)

    def publish(self, topic: str, payload: bytes | str) -> None:
        data = payload.encode() if isinstance(payload, str) else payload
        message = Message(topic, data)
        self.published.append(message)
        self._dispatcher.dispatch(message)

    def deliver(self, topic: str, payload: bytes | str) -> int:
        """Inject an inbound message, as if it arrived from a remote peer."""
        data = payload.encode() if isinstance(payload, str) else payload
        return self._dispatcher.dispatch(Message(topic, data))

    def close(self) -> None:
        self._connected = False

    @property
    def subscriptions(self) -> list[str]:
        return self._dispatcher.patterns

    @property
    def connected(self) -> bool:
        return self._connected
