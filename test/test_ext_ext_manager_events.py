"""Tests for the events dispatched by the `ext_manager` extension."""

from collections.abc import AsyncIterator
from logging import getLogger
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from blinker import Signal
from pytest import fixture
from trio import open_nursery, sleep

from flockwave.server.ext import ext_manager
from flockwave.server.ext.events.extension import EventsExtension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model.client import Client


class RecordingMessageHub(MessageHub):
    """Message hub that records outbound messages instead of sending them."""

    sent: list[tuple[Client, dict[str, Any]]]

    def __init__(self):
        super().__init__()
        self.sent = []

    def enqueue_message(self, message, to=None, in_response_to=None) -> None:
        self.sent.append((to, dict(message.body)))  # type: ignore

    def events_sent_to(self, client: Client) -> list[dict[str, Any]]:
        return [
            event for to, body in self.sent if to is client for event in body["events"]
        ]


class FakeExtensionManager:
    loaded = Signal()
    unloaded = Signal()

    def __init__(self):
        self.configure = MagicMock()


@fixture
def hub() -> RecordingMessageHub:
    return RecordingMessageHub()


@fixture
def manager() -> FakeExtensionManager:
    return FakeExtensionManager()


@fixture
def client() -> Client:
    return Client(_id="alice", _channel=MagicMock())


@fixture
async def events(
    hub: RecordingMessageHub, manager: FakeExtensionManager, client: Client
) -> AsyncIterator[EventsExtension]:
    events = EventsExtension()
    events.subscribe(client, "ext_manager:config")
    events.subscribe(client, "ext_manager:status")

    app = SimpleNamespace(
        client_registry=SimpleNamespace(removed=Signal()),
        extension_manager=manager,
        import_api=lambda name, *args: SimpleNamespace(use=events._use),
        message_hub=hub,
    )
    async with open_nursery() as nursery:
        nursery.start_soon(events.run, app, {}, getLogger(__name__))
        nursery.start_soon(ext_manager.run, app, {}, getLogger(__name__))
        await sleep(0)
        yield events
        nursery.cancel_scope.cancel()


def test_merge_extension_ids():
    merge = ext_manager.merge_extension_ids
    assert merge([], {"ids": ["b"]}) == [{"ids": ["b"]}]
    assert merge([{"ids": ["b", "c"]}], {"ids": ["a", "b"]}) == [
        {"ids": ["a", "b", "c"]}
    ]
    assert merge([{"ids": ["a"]}], None) == [{"ids": ["a"]}]


async def test_loading_and_unloading_dispatches_status_event(
    events: EventsExtension,
    hub: RecordingMessageHub,
    manager: FakeExtensionManager,
    client: Client,
    autojump_clock,
):
    manager.loaded.send(manager, name="rtk", extension=None)
    manager.loaded.send(manager, name="gps", extension=None)
    manager.unloaded.send(manager, name="rtk", extension=None)
    # The extension manager itself is not reported
    manager.loaded.send(manager, name="ext_manager", extension=None)
    await sleep(1)

    assert hub.events_sent_to(client) == [
        {"type": "ext_manager:status", "body": {"ids": ["gps", "rtk"]}}
    ]


async def test_setting_configuration_dispatches_config_event(
    events: EventsExtension,
    hub: RecordingMessageHub,
    manager: FakeExtensionManager,
    client: Client,
    autojump_clock,
):
    await ext_manager.set_configuration_of_extension(
        manager,  # type: ignore
        "rtk",
        {"presets": {}},
    )
    await sleep(1)

    manager.configure.assert_called_once_with("rtk", {"presets": {}})
    assert hub.events_sent_to(client) == [
        {"type": "ext_manager:config", "body": {"ids": ["rtk"]}}
    ]


async def test_no_events_after_shutdown(
    hub: RecordingMessageHub, manager: FakeExtensionManager
):
    # Without a running extension, the module-level dispatchers are unset
    assert ext_manager.config_events is None
    assert ext_manager.status_events is None

    await ext_manager.set_configuration_of_extension(
        manager,  # type: ignore
        "rtk",
        {},
    )
    manager.loaded.send(manager, name="rtk", extension=None)
    assert hub.sent == []
