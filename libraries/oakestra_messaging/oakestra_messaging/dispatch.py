import logging

from .bus import Handler, Message
from .topics import matches


class Dispatcher:
    """Routes inbound messages to handlers by topic pattern.

    Shared by every bus implementation so routing (and its exception isolation)
    is exercised once against the in-memory bus and reused by the real ones.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(__name__)
        # dict keeps insertion order and de-dupes patterns for free; a repeated
        # subscribe() on the same pattern just swaps in the new handler.
        self._handlers: dict[str, Handler] = {}

    def add(self, pattern: str, handler: Handler) -> None:
        self._handlers[pattern] = handler

    def remove(self, pattern: str) -> None:
        self._handlers.pop(pattern, None)

    @property
    def patterns(self) -> list[str]:
        return list(self._handlers.keys())

    def dispatch(self, message: Message) -> int:
        called = 0
        for pattern, handler in list(self._handlers.items()):
            if not matches(pattern, message.topic):
                continue
            called += 1
            try:
                handler(message)
            except Exception:
                # a broken handler must never take down the network thread or
                # stop other subscribers from receiving this message
                self._logger.exception(
                    "handler for pattern %r raised on topic %r", pattern, message.topic
                )
        return called
