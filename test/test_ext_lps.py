"""Tests for the `lps` extension."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import MagicMock

from pytest import fixture
from trio import open_nursery, sleep

from flockwave.server.ext.lps.examples import DummyLocalPositioningSystemType
from flockwave.server.ext.lps.extension import LocalPositioningSystemsExtension
from flockwave.server.registries import ObjectRegistry

from .recording_message_hub import RecordingMessageHub


@fixture
async def ext(
    hub: RecordingMessageHub,
) -> AsyncIterator[LocalPositioningSystemsExtension]:
    ext = LocalPositioningSystemsExtension()
    ext.app = SimpleNamespace(  # type: ignore
        command_execution_manager=MagicMock(),
        message_hub=hub,
        object_registry=ObjectRegistry(),
    )
    with ext._lps_type_registry.use("dummy", DummyLocalPositioningSystemType()):
        async with open_nursery() as nursery:
            nursery.start_soon(ext.run)
            await sleep(0)
            yield ext
            nursery.cancel_scope.cancel()


async def test_lps_updates_are_broadcast_until_removed(
    ext: LocalPositioningSystemsExtension, hub: RecordingMessageHub, autojump_clock
):
    with ext._lps_registry.create_and_use("dummy", id="lps1") as lps:
        # Adding the LPS triggers an initial X-LPS-INF notification
        await sleep(1)
        assert [list(body["status"]) for body in hub.broadcasts] == [["lps1"]]

        # Updates of the LPS are broadcast as well
        lps.notify_updated()
        await sleep(1)
        assert len(hub.broadcasts) == 2

    # Updates of a removed LPS are not broadcast any more
    lps.notify_updated()
    await sleep(1)
    assert len(hub.broadcasts) == 2
