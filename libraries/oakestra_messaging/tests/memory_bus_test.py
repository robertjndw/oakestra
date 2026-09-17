from oakestra_messaging import InMemoryBus, Message


def test_publish_records_bytes_payload():
    bus = InMemoryBus()
    bus.publish("nodes/n1/control/deploy", b"payload")

    assert bus.published == [Message("nodes/n1/control/deploy", b"payload")]


def test_publish_str_payload_is_utf8_encoded():
    bus = InMemoryBus()
    bus.publish("nodes/n1/control/deploy", "hello")

    assert bus.published == [Message("nodes/n1/control/deploy", b"hello")]


def test_publish_loops_back_to_matching_local_subscribers():
    bus = InMemoryBus()
    seen = []
    bus.subscribe("nodes/+/control/deploy", lambda m: seen.append(m.payload))

    bus.publish("nodes/n1/control/deploy", b"job")

    assert seen == [b"job"]


def test_publish_does_not_loop_back_to_non_matching_subscribers():
    bus = InMemoryBus()
    seen = []
    bus.subscribe("nodes/+/information", lambda m: seen.append(m.payload))

    bus.publish("nodes/n1/control/deploy", b"job")

    assert seen == []


def test_subscribe_before_connect_is_allowed_and_active():
    bus = InMemoryBus()
    seen = []
    bus.subscribe("nodes/+/information", lambda m: seen.append(m.topic))
    # no connect() call yet
    bus.deliver("nodes/n1/information", b"{}")

    assert seen == ["nodes/n1/information"]


def test_deliver_dispatches_synchronously_and_returns_handler_count():
    bus = InMemoryBus()
    bus.subscribe("nodes/+/information", lambda m: None)
    bus.subscribe("nodes/n1/information", lambda m: None)

    called = bus.deliver("nodes/n1/information", "{}")

    assert called == 2


def test_deliver_does_not_appear_in_published():
    bus = InMemoryBus()
    bus.deliver("nodes/n1/information", b"{}")

    assert bus.published == []


def test_subscriptions_are_unique_patterns():
    bus = InMemoryBus()
    bus.subscribe("a", lambda m: None)
    bus.subscribe("b", lambda m: None)
    bus.subscribe("a", lambda m: None)

    assert bus.subscriptions == ["a", "b"]


def test_unsubscribe_stops_delivery():
    bus = InMemoryBus()
    seen = []
    bus.subscribe("a", lambda m: seen.append(m.topic))
    bus.unsubscribe("a")

    bus.deliver("a", b"")

    assert seen == []


def test_connected_reflects_connect_and_close():
    bus = InMemoryBus()
    assert bus.connected is False

    bus.connect()
    assert bus.connected is True

    bus.close()
    assert bus.connected is False


def test_handler_exception_is_isolated():
    bus = InMemoryBus()
    seen = []

    def broken(message):
        raise ValueError("boom")

    bus.subscribe("a", broken)
    bus.subscribe("a", lambda m: seen.append("ok"))  # re-subscribe replaces the handler for "a"
    bus.subscribe("b", broken)
    bus.subscribe("c", lambda m: seen.append("c-ok"))

    # "a" now only has the working handler (last subscribe wins); "b" is
    # broken and independent from "c"
    bus.deliver("a", b"")
    called = bus.deliver("b", b"")

    assert called == 1
    assert seen == ["ok"]

    # a broken handler on one topic must not affect delivery to another
    bus.deliver("c", b"")
    assert seen == ["ok", "c-ok"]
