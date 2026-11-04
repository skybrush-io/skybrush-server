"""Tests for the `events` extension."""

from collections.abc import AsyncIterator, Sequence
from logging import getLogger
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from blinker import Signal
from pytest import fixture, raises
from trio import open_nursery, sleep

from flockwave.server.ext.events import keep_all, keep_first
from flockwave.server.ext.events.extension import EventsExtension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage


class RecordingMessageHub(MessageHub):
    """Message hub that records outbound messages instead of sending them."""

    sent: list[tuple[Client, dict[str, Any]]]

    def __init__(self):
        super().__init__()
        self.sent = []

    def enqueue_message(self, message, to=None, in_response_to=None) -> None:
        assert isinstance(message, FlockwaveMessage)
        assert isinstance(to, Client)
        self.sent.append((to, dict(message.body)))

    def events_sent_to(self, client: Client) -> list[list[dict[str, Any]]]:
        return [body["events"] for to, body in self.sent if to is client]


class FakeClientRegistry:
    removed = Signal()


def create_client(id: str) -> Client:
    return Client(_id=id, _channel=MagicMock())


@fixture
def hub() -> RecordingMessageHub:
    return RecordingMessageHub()


@fixture
def registry() -> FakeClientRegistry:
    return FakeClientRegistry()


@fixture
async def ext(
    hub: RecordingMessageHub, registry: FakeClientRegistry
) -> AsyncIterator[EventsExtension]:
    ext = EventsExtension()
    app = SimpleNamespace(client_registry=registry, message_hub=hub)
    async with open_nursery() as nursery:
        nursery.start_soon(ext.run, app, {}, getLogger(__name__))
        await sleep(0)
        yield ext
        nursery.cancel_scope.cancel()


async def test_events_are_sent_only_to_subscribers(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice, bob = create_client("alice"), create_client("bob")
    ext.subscribe(alice, "show:config")
    ext.subscribe(bob, "show:lights")

    with ext._use("show:config") as dispatcher:
        dispatcher.dispatch()
        await sleep(1)

    assert hub.events_sent_to(alice) == [[{"type": "show:config"}]]
    assert hub.events_sent_to(bob) == []
    assert hub.sent[0][1]["type"] == "EVT-INF"


async def test_events_without_subscribers_are_dropped(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    with ext._use("show:config") as dispatcher:
        assert not dispatcher.has_subscribers
        dispatcher.dispatch()
        await sleep(1)

        # Subscribing later must not deliver the event dispatched earlier
        alice = create_client("alice")
        ext.subscribe(alice, "show:config")
        assert dispatcher.has_subscribers
        await sleep(1)

    assert hub.sent == []


async def test_keep_latest_is_the_default_merging_policy(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")
    ext.subscribe(alice, "show:lights")

    with ext._use("show:config") as config, ext._use("show:lights") as lights:
        for i in range(10):
            lights.dispatch({"index": i})
            await sleep(0.005)
        config()
        await sleep(1)

    # One notification with both events; only the last body of show:lights
    # is retained
    assert hub.events_sent_to(alice) == [
        [{"type": "show:lights", "body": {"index": 9}}, {"type": "show:config"}]
    ]


async def test_keep_first_merging_policy(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "test:first")

    with ext._use("test:first", merging_policy=keep_first) as dispatcher:
        for i in range(3):
            dispatcher.dispatch(i)
        await sleep(1)

    assert hub.events_sent_to(alice) == [[{"type": "test:first", "body": 0}]]


async def test_keep_all_merging_policy(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "test:all")

    with ext._use("test:all", merging_policy=keep_all) as dispatcher:
        for i in range(3):
            dispatcher.dispatch(i)
        await sleep(1)

    assert hub.events_sent_to(alice) == [
        [{"type": "test:all", "body": i} for i in range(3)]
    ]


async def test_custom_merging_policy(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    def sum_values(pending: Sequence[Any], body: Any) -> Sequence[Any]:
        total = sum(pending) + body
        return [total] if total else []

    alice = create_client("alice")
    ext.subscribe(alice, "test:sum")

    with ext._use("test:sum", merging_policy=sum_values) as dispatcher:
        dispatcher.dispatch(2)
        dispatcher.dispatch(3)
        await sleep(1)

        # Events cancelling each other out produce no notification
        dispatcher.dispatch(4)
        dispatcher.dispatch(-4)
        await sleep(1)

    assert hub.events_sent_to(alice) == [[{"type": "test:sum", "body": 5}]]


async def test_separate_bursts_produce_separate_notifications(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")

    with ext._use("show:config") as dispatcher:
        dispatcher.dispatch()
        await sleep(1)
        dispatcher.dispatch()
        await sleep(1)

    assert len(hub.events_sent_to(alice)) == 2


async def test_coalescing_delay_is_configurable(
    hub: RecordingMessageHub, registry: FakeClientRegistry, autojump_clock
):
    ext = EventsExtension()
    app = SimpleNamespace(client_registry=registry, message_hub=hub)
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")

    async with open_nursery() as nursery:
        nursery.start_soon(ext.run, app, {"coalescing_delay": 5}, getLogger())
        with ext._use("show:config") as dispatcher:
            dispatcher.dispatch()
            await sleep(4)
            assert hub.sent == []
            await sleep(2)
            assert len(hub.sent) == 1
        nursery.cancel_scope.cancel()


async def test_registration_lifecycle(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")

    with ext._use("show:config") as dispatcher:
        assert dispatcher.active
        assert dispatcher.type == "show:config"

        # Registering the same event type twice is not allowed
        with raises(KeyError):
            with ext._use("show:config"):
                pass

        # Pending events are dropped when the event type is unregistered
        dispatcher.dispatch()

    assert not dispatcher.active
    await sleep(1)
    assert hub.sent == []

    # Inactive dispatchers do nothing
    dispatcher.dispatch()
    await sleep(1)
    assert hub.sent == []

    # The event type can be registered again
    with ext._use("show:config") as new_dispatcher:
        dispatcher.dispatch()
        new_dispatcher.dispatch()
        await sleep(1)

    assert hub.events_sent_to(alice) == [[{"type": "show:config"}]]

    with raises(ValueError):
        with ext._use(""):
            pass


async def test_subscriptions_are_idempotent(
    ext: EventsExtension, hub: RecordingMessageHub, autojump_clock
):
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")
    ext.subscribe(alice, "show:config")
    assert ext.list_subscriptions(alice) == ["show:config"]

    ext.unsubscribe(alice, "show:config")
    assert ext.list_subscriptions(alice) == []
    assert not ext.has_subscribers("show:config")

    # Unsubscribing again is a no-op
    ext.unsubscribe(alice, "show:config")


async def test_subscriptions_removed_on_disconnect(
    ext: EventsExtension,
    hub: RecordingMessageHub,
    registry: FakeClientRegistry,
    autojump_clock,
):
    alice, bob = create_client("alice"), create_client("bob")
    ext.subscribe(alice, "show:config")
    ext.subscribe(alice, "show:lights")
    ext.subscribe(bob, "show:config")

    registry.removed.send(registry, client=alice)

    assert ext.list_subscriptions(alice) == []
    assert ext.list_subscriptions(bob) == ["show:config"]
    assert not ext.has_subscribers("show:lights")


async def test_client_disconnecting_between_dispatch_and_flush(
    ext: EventsExtension,
    hub: RecordingMessageHub,
    registry: FakeClientRegistry,
    autojump_clock,
):
    alice = create_client("alice")
    ext.subscribe(alice, "show:config")

    with ext._use("show:config") as dispatcher:
        dispatcher.dispatch()
        registry.removed.send(registry, client=alice)
        await sleep(1)

    assert hub.sent == []


async def test_EVT_SUB_and_EVT_UNSUB_handlers(
    ext: EventsExtension, hub: RecordingMessageHub
):
    alice = create_client("alice")

    request = hub._message_builder.create_message(
        {"type": "EVT-SUB", "types": ["show:config", "", 42, "custom"]}
    )
    response = ext.handle_EVT_SUB(request, alice, hub)
    assert response.body["type"] == "EVT-SUB"
    assert response.body["result"] == {"show:config": True, "custom": True}
    assert set(response.body["error"].keys()) == {"", "42"}
    assert ext.list_subscriptions(alice) == ["custom", "show:config"]

    request = hub._message_builder.create_message(
        {"type": "EVT-UNSUB", "types": ["custom", "not-subscribed"]}
    )
    response = ext.handle_EVT_UNSUB(request, alice, hub)
    assert response.body["type"] == "EVT-UNSUB"
    assert response.body["result"] == {"custom": True, "not-subscribed": True}
    assert "error" not in response.body
    assert ext.list_subscriptions(alice) == ["show:config"]


async def test_handlers_are_registered_in_message_hub(
    ext: EventsExtension, hub: RecordingMessageHub
):
    assert hub._handlers_by_type["EVT-SUB"]
    assert hub._handlers_by_type["EVT-UNSUB"]
