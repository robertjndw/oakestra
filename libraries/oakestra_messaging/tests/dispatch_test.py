import logging

from oakestra_messaging import Dispatcher, Message


def test_dispatch_calls_matching_handlers_and_counts_them():
    dispatcher = Dispatcher()
    seen = []
    dispatcher.add("nodes/+/information", lambda m: seen.append(("info", m.topic)))
    dispatcher.add("nodes/+/job", lambda m: seen.append(("job", m.topic)))

    called = dispatcher.dispatch(Message("nodes/n1/information", b"{}"))

    assert called == 1
    assert seen == [("info", "nodes/n1/information")]


def test_dispatch_calls_every_matching_pattern():
    dispatcher = Dispatcher()
    seen = []
    dispatcher.add("nodes/+/net/#", lambda m: seen.append("wide"))
    dispatcher.add("nodes/+/net/subnet", lambda m: seen.append("narrow"))

    called = dispatcher.dispatch(Message("nodes/n1/net/subnet", b"{}"))

    assert called == 2
    assert set(seen) == {"wide", "narrow"}


def test_dispatch_returns_zero_for_unmatched_topic():
    dispatcher = Dispatcher()
    dispatcher.add("nodes/+/information", lambda m: None)

    assert dispatcher.dispatch(Message("jobs/j1/updates_available", b"{}")) == 0


def test_patterns_are_unique_and_keep_insertion_order():
    dispatcher = Dispatcher()
    dispatcher.add("a", lambda m: None)
    dispatcher.add("b", lambda m: None)
    dispatcher.add("a", lambda m: None)  # re-subscribe, same pattern

    assert dispatcher.patterns == ["a", "b"]


def test_remove_stops_delivery():
    dispatcher = Dispatcher()
    seen = []
    dispatcher.add("a", lambda m: seen.append(m.topic))
    dispatcher.remove("a")

    assert dispatcher.dispatch(Message("a", b"")) == 0
    assert seen == []


def test_remove_unknown_pattern_is_a_no_op():
    dispatcher = Dispatcher()
    dispatcher.remove("does-not-exist")  # must not raise


def test_handler_exception_is_isolated_and_logged(caplog):
    dispatcher = Dispatcher(logging.getLogger("test-dispatch"))

    def broken(message):
        raise ValueError("boom")

    dispatcher.add("a", broken)

    with caplog.at_level(logging.ERROR, logger="test-dispatch"):
        called = dispatcher.dispatch(Message("a", b""))

    assert called == 1
    assert any(record.exc_info for record in caplog.records)


def test_handler_exception_does_not_stop_other_matching_handlers():
    dispatcher = Dispatcher(logging.getLogger("test-dispatch-2"))
    seen = []

    dispatcher.add("nodes/+/information", lambda m: (_ for _ in ()).throw(ValueError("boom")))
    dispatcher.add("nodes/n1/information", lambda m: seen.append("second"))

    called = dispatcher.dispatch(Message("nodes/n1/information", b"{}"))

    assert called == 2
    assert seen == ["second"]
