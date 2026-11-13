"""Tests for the `lps` extension."""

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any, TypedDict
from unittest.mock import MagicMock

import fastjsonschema
from flockwave.encoders.json import create_json_encoder
from flockwave.gps.vectors import PositionXYZ
from flockwave.spec.errors import FlockwaveErrorCode
from pytest import fixture, raises
from trio import open_nursery, sleep
from trio.testing import wait_all_tasks_blocked

from flockwave.server.commands import CommandExecutionManager
from flockwave.server.ext.lps.extension import LocalPositioningSystemsExtension
from flockwave.server.ext.lps.model import (
    Anchor,
    LocalPositioningSystem,
    LocalPositioningSystemType,
)
from flockwave.server.ext.lps.registry import (
    LocalPositioningSystemRegistry,
    LocalPositioningSystemTypeRegistry,
)
from flockwave.server.ext.lps_dummy import (
    DummyLocalPositioningSystem,
    DummyLocalPositioningSystemType,
)
from flockwave.server.model.builders import FlockwaveMessageBuilder
from flockwave.server.model.client import Client
from flockwave.server.model.commands import CommandExecutionStatus
from flockwave.server.model.messages import FlockwaveResponse
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
            await wait_all_tasks_blocked()
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


async def test_lps_handlers_are_disconnected_on_shutdown(hub: RecordingMessageHub):
    object_registry = ObjectRegistry()
    ext = LocalPositioningSystemsExtension()
    ext.app = SimpleNamespace(  # type: ignore
        command_execution_manager=MagicMock(),
        message_hub=hub,
        object_registry=object_registry,
    )
    with ext._lps_type_registry.use("dummy", DummyLocalPositioningSystemType()):
        async with open_nursery() as nursery:
            nursery.start_soon(ext.run)
            await wait_all_tasks_blocked()
            lps = ext._lps_registry.create("dummy", id="lps1")
            assert list(LocalPositioningSystem.on_updated.receivers_for(lps))
            nursery.cancel_scope.cancel()

    # The LPS left in the registry is removed when the extension shuts down,
    # and its update signal is disconnected from the extension
    assert "lps1" not in ext._lps_registry
    assert "lps1" not in object_registry
    assert not list(LocalPositioningSystem.on_updated.receivers_for(lps))
    assert ext._lps_to_update is None


def test_lps_features_follow_overridden_methods():
    class BareLocalPositioningSystem(LocalPositioningSystem):
        pass

    assert BareLocalPositioningSystem().features == []
    assert DummyLocalPositioningSystem().features == ["calibrate", "configure"]
    assert DummyLocalPositioningSystem().json["features"] == [
        "calibrate",
        "configure",
    ]


class XConfiguration(TypedDict):
    x: float


class AsyncConfigurableLocalPositioningSystem(LocalPositioningSystem[XConfiguration]):
    cfg: XConfiguration | None = None

    async def _configure_inner(self, cfg: XConfiguration) -> None:
        await sleep(1)
        self.cfg = cfg

    async def get_configuration(self) -> XConfiguration:
        assert self.cfg is not None
        return self.cfg


async def test_lps_configuration_is_validated_before_it_is_applied(autojump_clock):
    lps = AsyncConfigurableLocalPositioningSystem()
    lps._schema_validator_getter = lambda: fastjsonschema.compile(
        {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        }
    )

    # Invalid configurations are rejected without being applied
    with raises(RuntimeError, match="Invalid configuration"):
        await lps.configure({"x": "foo"})
    assert lps.cfg is None

    # Valid configurations are applied
    await lps.configure({"x": 1})
    assert lps.cfg == {"x": 1}
    assert (await lps.get_configuration())["x"] == 1


async def test_lps_without_configuration_support_fails_when_awaited():
    class BareLocalPositioningSystem(LocalPositioningSystem):
        pass

    lps = BareLocalPositioningSystem()
    lps._schema_validator_getter = lambda: fastjsonschema.compile({})

    with raises(NotImplementedError):
        await lps.configure({"anything": "goes"})


def test_lps_registry_remove_by_id_returns_removed_lps():
    registry = LocalPositioningSystemRegistry(LocalPositioningSystemTypeRegistry())
    with (
        registry._lps_type_registry.use("dummy", DummyLocalPositioningSystemType()),
        registry.use_object_registry(ObjectRegistry()),
    ):
        lps = registry.create("dummy", id="lps1")
        assert registry.remove_by_id("lps1") is lps
        assert registry.remove_by_id("lps1") is None


def test_anchor_json():
    anchor = Anchor(id="a1")
    assert anchor.json == {
        "id": "a1",
        "active": True,
        "position": None,
        "attitude": None,
        "battery": None,
        "errors": anchor.errors,
    }
    assert anchor.errors.json == []

    anchor.position = PositionXYZ(1, 2.5, 3)
    anchor.attitude = (0.5, 0.5, -0.5, 0.5)
    anchor.errors.ensure(66)
    json = anchor.json
    assert json["position"].json == [1000, 2500, 3000]
    assert json["attitude"] == [0.5, 0.5, -0.5, 0.5]
    assert json["errors"].json == [66]


def test_anchors_have_separate_error_sets():
    first, second = Anchor(id="a1"), Anchor(id="a2")
    first.errors.ensure(66)
    assert second.errors.json == []


@fixture
def client() -> Client:
    return Client(_id="alice", _channel=MagicMock())


@fixture
def cmd_manager() -> CommandExecutionManager:
    return CommandExecutionManager()


@fixture
def finished_commands(
    cmd_manager: CommandExecutionManager,
) -> Iterator[dict[str, CommandExecutionStatus]]:
    """Dictionary that maps receipt IDs of finished async operations to their
    status objects.
    """
    result: dict[str, CommandExecutionStatus] = {}

    def on_finished(sender, status: CommandExecutionStatus) -> None:
        result[status.id] = status

    with cmd_manager.finished.connected_to(on_finished, sender=cmd_manager):
        yield result


@fixture
async def ext_with_cmd_manager(
    hub: RecordingMessageHub, cmd_manager: CommandExecutionManager
) -> AsyncIterator[LocalPositioningSystemsExtension]:
    ext = LocalPositioningSystemsExtension()
    ext.app = SimpleNamespace(  # type: ignore
        command_execution_manager=cmd_manager,
        message_hub=hub,
        object_registry=ObjectRegistry(),
    )
    with ext._lps_type_registry.use("dummy", DummyLocalPositioningSystemType()):
        async with open_nursery() as nursery:
            nursery.start_soon(cmd_manager.run)
            nursery.start_soon(ext.run)
            await wait_all_tasks_blocked()
            yield ext
            nursery.cancel_scope.cancel()


async def send_request(
    hub: RecordingMessageHub, client: Client, body: dict[str, Any]
) -> FlockwaveResponse:
    """Feeds a request to the handler registered for its type in the message
    hub, and starts the async operations of the response as if the response was
    sent to the client.
    """
    message = FlockwaveMessageBuilder().create_message(body)
    (handler,) = hub._handlers_by_type[body["type"]]
    response = handler(message, client, hub)
    assert isinstance(response, FlockwaveResponse)
    await response._notify_sent()
    await sleep(0.1)
    return response


async def test_lps_configuration_can_be_written_and_read_back(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    finished_commands: dict[str, CommandExecutionStatus],
    autojump_clock,
):
    ext = ext_with_cmd_manager
    cfg = {"width": 6.0, "depth": 3.0, "height": 2.0, "simulate_faults": False}
    with ext._lps_registry.create_and_use("dummy", id="lps1"):
        response = await send_request(
            hub,
            client,
            {"type": "X-LPS-SETCFG", "ids": ["lps1"], "configuration": cfg},
        )
        receipt = response.body["receipt"]["lps1"]
        assert finished_commands[receipt].error is None

        response = await send_request(
            hub, client, {"type": "X-LPS-CFG", "ids": ["lps1", "nonexistent"]}
        )
        assert "nonexistent" in response.body["error"]
        receipt = response.body["receipt"]["lps1"]
        assert finished_commands[receipt].result == cfg


async def test_lps_invalid_configuration_is_rejected_asynchronously(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    finished_commands: dict[str, CommandExecutionStatus],
    autojump_clock,
):
    ext = ext_with_cmd_manager
    with ext._lps_registry.create_and_use("dummy", id="lps1"):
        response = await send_request(
            hub,
            client,
            {"type": "X-LPS-SETCFG", "ids": ["lps1"], "configuration": {"x": 1}},
        )
        receipt = response.body["receipt"]["lps1"]
        assert "Invalid configuration" in str(finished_commands[receipt].error)


async def test_lps_configuration_request_without_configuration_fails(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    autojump_clock,
):
    ext = ext_with_cmd_manager
    with ext._lps_registry.create_and_use("dummy", id="lps1"):
        response = await send_request(
            hub, client, {"type": "X-LPS-SETCFG", "ids": ["lps1"]}
        )
        assert "does not contain configuration" in response.body["error"]["lps1"]
        assert "lps1" not in response.body.get("receipt", {})


def test_anchor_error_codes_are_encoded_as_integers():
    anchor = Anchor(id="a1")
    anchor.errors.ensure(FlockwaveErrorCode.UNSPECIFIED_WARNING)
    assert b'"errors":[127]' in create_json_encoder()(anchor)


async def test_lps_calibration_can_be_suspended_and_resumed(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    cmd_manager: CommandExecutionManager,
    finished_commands: dict[str, CommandExecutionStatus],
    autojump_clock,
):
    ext = ext_with_cmd_manager
    suspended: list[str] = []

    def on_suspended(sender, status: CommandExecutionStatus) -> None:
        suspended.append(status.id)

    with (
        cmd_manager.suspended.connected_to(on_suspended, sender=cmd_manager),
        ext._lps_registry.create_and_use("dummy", id="lps1"),
    ):
        response = await send_request(
            hub,
            client,
            {
                "type": "X-LPS-CALIB",
                "ids": ["lps1"],
                "parameters": {"sample_duration": 1},
            },
        )
        receipt = response.body["receipt"]["lps1"]

        for step in range(3):
            assert suspended == [receipt] * (step + 1)
            assert receipt not in finished_commands
            cmd_manager.resume(receipt, True)
            await sleep(5)

        status = finished_commands[receipt]
        assert status.error is None
        assert status.progress is not None and status.progress.percentage == 100


async def test_lps_calibration_parameters_must_be_an_object(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    autojump_clock,
):
    ext = ext_with_cmd_manager
    with ext._lps_registry.create_and_use("dummy", id="lps1"):
        response = await send_request(
            hub,
            client,
            {"type": "X-LPS-CALIB", "ids": ["lps1"], "parameters": [1, 2]},
        )
        assert "dictionary of calibration parameters" in response.body["error"]["lps1"]
        assert "lps1" not in response.body.get("receipt", {})


async def test_lps_without_calibration_support_fails_immediately(
    ext_with_cmd_manager: LocalPositioningSystemsExtension,
    hub: RecordingMessageHub,
    client: Client,
    autojump_clock,
):
    class BareLocalPositioningSystem(LocalPositioningSystem):
        pass

    class BareLocalPositioningSystemType(
        LocalPositioningSystemType[BareLocalPositioningSystem]
    ):
        description = name = "Bare LPS"

        def create(self) -> BareLocalPositioningSystem:
            return BareLocalPositioningSystem()

        def get_configuration_schema(self) -> dict[str, Any]:
            return {}

    ext = ext_with_cmd_manager
    with (
        ext._lps_type_registry.use("bare", BareLocalPositioningSystemType()),
        ext._lps_registry.create_and_use("bare", id="lps1"),
    ):
        response = await send_request(
            hub, client, {"type": "X-LPS-CALIB", "ids": ["lps1"]}
        )
        assert response.body["error"]["lps1"] == (
            "This local positioning system cannot be calibrated"
        )
        assert "lps1" not in response.body.get("receipt", {})
