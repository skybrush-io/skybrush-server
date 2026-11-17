"""Tests for the Loco local positioning system of the `crazyflie` extension."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from aiocflib.crazyflie.loco import Vector3D
from flockwave.spec.errors import FlockwaveErrorCode
from pytest import mark, raises
from trio import current_time, open_nursery, sleep

from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.crazyflie.driver import CrazyflieUAV
from flockwave.server.ext.crazyflie.loco_lps import (
    LOCO_LPS_ID,
    VERIFY_DELAY,
    WRITE_ATTEMPT_COUNT,
    LocoLocalPositioningSystem,
    LocoLocalPositioningSystemType,
)

from .crazyflie_extension import running_crazyflie_extension
from .recording_message_hub import RecordingMessageHub


class FakeAnchorNetwork:
    """Fake set of Loco anchors that store their own positions."""

    def __init__(self, positions: dict[int, Vector3D | None]):
        self.positions = positions
        self.dropped_packets: dict[int, int] = {}
        """Number of packets that each anchor misses before it receives one."""
        self.latency = 0.0
        """Number of seconds before the drones hear about a new position."""
        self._pending: list[tuple[float, int, Vector3D]] = []

    def get_positions(self) -> dict[int, Vector3D | None]:
        now = current_time()
        for due, anchor_id, position in self._pending:
            if due <= now:
                self.positions[anchor_id] = position
        self._pending = [item for item in self._pending if item[0] > now]
        return dict(self.positions)

    def receive(self, anchor_id: int, position: Vector3D) -> None:
        if anchor_id not in self.positions:
            return
        if self.dropped_packets.get(anchor_id, 0) > 0:
            self.dropped_packets[anchor_id] -= 1
            return
        self._pending.append((current_time() + self.latency, anchor_id, position))


class FakeUAV:
    """Fake Crazyflie UAV that sees all the anchors of an anchor network."""

    def __init__(self, id: str, network: FakeAnchorNetwork | None):
        self.id = id
        self.network = network
        self.error: Exception | None = None
        self.is_idle_on_ground = True
        self.packets: list[tuple[int, Vector3D]] = []
        self.reads = 0

    async def get_loco_anchor_positions(self) -> dict[int, Vector3D | None]:
        await sleep(0.1)
        if self.network is None:
            raise NotSupportedError("No Loco positioning deck")
        if self.error:
            raise self.error
        self.reads += 1
        return self.network.get_positions()

    async def set_loco_anchor_position(
        self, anchor_id: int, position: Vector3D
    ) -> bool:
        assert self.network is not None
        self.packets.append((anchor_id, position))
        self.network.receive(anchor_id, position)
        return True


def create_lps(*uavs: FakeUAV) -> LocoLocalPositioningSystem:
    connected = list(uavs)
    lps_type = LocoLocalPositioningSystemType(lambda: connected, MagicMock())
    lps = lps_type.create()
    lps._id = "loco"
    lps._schema_validator_getter = lps_type.get_configuration_schema_validator
    return lps


def configuration(**positions: Vector3D) -> dict[str, Any]:
    return {
        anchor_id.removeprefix("a"): position
        for anchor_id, position in positions.items()
    }


def test_loco_lps_features():
    lps = create_lps()
    assert lps.name == "Loco"
    assert list(lps.features) == ["configure"]


@mark.parametrize(
    "cfg",
    [
        {"256": [0, 0, 0]},
        {"01": [0, 0, 0]},
        {"1": [0, 0]},
        {"1": [0, 0, 0, 0]},
        {"1": ["0", 0, 0]},
        {"1": {"x": 0, "y": 0, "z": 0}},
    ],
)
async def test_loco_lps_rejects_invalid_configuration(cfg: dict[str, Any]):
    with raises(RuntimeError, match="Invalid configuration"):
        await create_lps().configure(cfg)


async def test_loco_lps_reads_anchors_from_first_drone_with_deck(autojump_clock):
    network = FakeAnchorNetwork({7: (4.0, 5.0, 6.0), 0: (1.0, 2.0, 3.0), 3: None})
    no_deck, first, second = (
        FakeUAV("01", None),
        FakeUAV("02", network),
        FakeUAV("03", network),
    )
    lps = create_lps(no_deck, first, second)
    updates = []

    with lps.on_updated.connected_to(updates.append, sender=lps):
        assert await lps.get_configuration() == configuration(
            a0=(1.0, 2.0, 3.0), a7=(4.0, 5.0, 6.0)
        )
        await lps.get_configuration()

    assert (first.reads, second.reads) == (2, 0)
    assert len(updates) == 1

    assert [anchor.id for anchor in lps.anchors] == ["0", "3", "7"]
    known, unknown, _ = lps.anchors
    assert known.position is not None
    assert known.position.json == [1000, 2000, 3000]
    assert known.active
    assert unknown.position is None
    assert not unknown.active
    assert FlockwaveErrorCode.CONFIGURATION_ERROR in unknown.errors


async def test_loco_lps_reads_anchors_from_first_connected_drone(autojump_clock):
    network = FakeAnchorNetwork({1: (1.0, 2.0, 3.0)})
    no_deck = FakeUAV("01", None)
    broken = FakeUAV("02", network)
    broken.error = OSError("Communication error")
    first, second, third = (
        FakeUAV("03", network),
        FakeUAV("04", network),
        FakeUAV("05", network),
    )
    lps = create_lps()
    updates = []

    with lps.on_updated.connected_to(updates.append, sender=lps):
        # Drones without a deck or with a failed read do not count
        await lps.handle_uav_connected(no_deck)
        await lps.handle_uav_connected(broken)
        assert lps.anchors == []

        # Only one of the drones that connect at the same time reads the anchors
        async with open_nursery() as nursery:
            nursery.start_soon(lps.handle_uav_connected, first)
            nursery.start_soon(lps.handle_uav_connected, second)

        # Later connections do not read the anchors again
        await lps.handle_uav_connected(third)
        await lps.handle_uav_connected(first)

    assert [uav.reads for uav in (first, second, third)] in ([1, 0, 0], [0, 1, 0])
    assert [anchor.id for anchor in lps.anchors] == ["1"]
    assert len(updates) == 1


async def test_loco_lps_skips_drones_that_fail_to_respond(autojump_clock):
    network = FakeAnchorNetwork({1: (1.0, 2.0, 3.0)})
    broken, working = FakeUAV("01", network), FakeUAV("02", network)
    broken.error = OSError("Communication error")
    lps = create_lps(broken, working)
    assert isinstance(lps.log, MagicMock)

    assert await lps.get_configuration() == configuration(a1=(1.0, 2.0, 3.0))
    lps.log.warning.assert_called_once()
    assert "Communication error" in lps.log.warning.call_args.args[0]
    assert lps.log.warning.call_args.kwargs["extra"] == {"id": "01"}


async def test_loco_lps_keeps_last_known_anchors_without_drones(autojump_clock):
    uavs = [FakeUAV("01", FakeAnchorNetwork({1: (1.0, 2.0, 3.0)}))]
    lps = create_lps()
    lps._uav_getter = lambda: uavs

    await lps.get_configuration()
    uavs.clear()

    assert await lps.get_configuration() == configuration(a1=(1.0, 2.0, 3.0))
    assert [anchor.id for anchor in lps.anchors] == ["1"]


async def test_loco_lps_writes_anchor_positions_through_one_drone(autojump_clock):
    network = FakeAnchorNetwork({1: (0.0, 0.0, 0.0), 2: None, 3: (3.0, 3.0, 3.0)})
    no_deck, relay, other = (
        FakeUAV("01", None),
        FakeUAV("02", network),
        FakeUAV("03", network),
    )
    lps = create_lps(no_deck, relay, other)

    # Configuration arrives from JSON, with lists instead of tuples
    await lps.configure(
        {"1": [1.0, 1.5, 2.0], "2": [-2.0, 2.0, 0.1], "3": [3.0, 3.0, 3.0]}
    )

    # Anchors that already have the requested position are not written
    assert relay.packets == [(1, (1.0, 1.5, 2.0)), (2, (-2.0, 2.0, 0.1))]
    assert other.packets == []
    assert (relay.reads, other.reads) == (2, 0)

    assert await lps.get_configuration() == configuration(
        a1=(1.0, 1.5, 2.0), a2=(-2.0, 2.0, 0.1), a3=(3.0, 3.0, 3.0)
    )
    assert all(anchor.active for anchor in lps.anchors)


async def test_loco_lps_resends_unconfirmed_positions(autojump_clock):
    network = FakeAnchorNetwork({1: None, 2: None})
    network.dropped_packets = {2: WRITE_ATTEMPT_COUNT - 1}
    uav = FakeUAV("01", network)
    lps = create_lps(uav)

    await lps.configure(configuration(a1=(1.0, 1.0, 1.0), a2=(2.0, 2.0, 2.0)))

    assert (
        uav.packets
        == [(1, (1.0, 1.0, 1.0))] + [(2, (2.0, 2.0, 2.0))] * WRITE_ATTEMPT_COUNT
    )


async def test_loco_lps_waits_for_anchors_before_verifying(autojump_clock):
    network = FakeAnchorNetwork({1: None})
    network.latency = VERIFY_DELAY * 0.9
    uav = FakeUAV("01", network)
    lps = create_lps(uav)

    await lps.configure(configuration(a1=(1.0, 1.0, 1.0)))

    assert uav.packets == [(1, (1.0, 1.0, 1.0))]


async def test_loco_lps_reports_unconfirmed_anchors(autojump_clock):
    network = FakeAnchorNetwork({1: None, 2: None, 3: None})
    network.dropped_packets = {2: WRITE_ATTEMPT_COUNT, 3: WRITE_ATTEMPT_COUNT}
    uav = FakeUAV("01", network)
    lps = create_lps(uav)

    with raises(RuntimeError, match="did not confirm.*: 2, 3$"):
        await lps.configure(
            configuration(a1=(1.0, 1.0, 1.0), a2=(2.0, 2.0, 2.0), a3=(3.0, 3.0, 3.0))
        )

    assert len(uav.packets) == 1 + 2 * WRITE_ATTEMPT_COUNT

    # The anchor that received its position is updated nevertheless
    assert await lps.get_configuration() == configuration(a1=(1.0, 1.0, 1.0))


async def test_loco_lps_does_not_write_while_drones_are_flying(autojump_clock):
    network = FakeAnchorNetwork({1: None})
    idle, flying = FakeUAV("01", network), FakeUAV("02", network)
    flying.is_idle_on_ground = False
    lps = create_lps(idle, flying)

    with raises(RuntimeError, match="Drones not ready: 02$"):
        await lps.configure(configuration(a1=(1.0, 1.0, 1.0)))

    assert idle.packets == flying.packets == []


@mark.parametrize("has_drone_without_deck", [False, True])
async def test_loco_lps_needs_drone_with_deck_for_writing(
    autojump_clock, has_drone_without_deck: bool
):
    lps = create_lps(*([FakeUAV("01", None)] if has_drone_without_deck else []))

    with raises(RuntimeError, match="at least one drone"):
        await lps.configure(configuration(a1=(1.0, 1.0, 1.0)))


async def test_loco_lps_ignores_empty_configuration(autojump_clock):
    uav = FakeUAV("01", FakeAnchorNetwork({1: None}))
    lps = create_lps(uav)
    await lps.configure({})
    assert uav.reads == 0
    assert lps.anchors == []


async def test_extension_uses_connected_drones_for_loco_lps(
    hub: RecordingMessageHub, tmp_path: Path
):
    async with running_crazyflie_extension(hub, tmp_path, "uwb") as (
        ext,
        lps_ext,
        driver,
    ):
        assert ext.app is not None
        connected = CrazyflieUAV("01", driver=driver)
        connected._crazyflie = MagicMock()
        disconnected = CrazyflieUAV("02", driver=driver)
        other_driver = CrazyflieUAV("03", driver=MagicMock())
        other_driver._crazyflie = MagicMock()
        for uav in (connected, disconnected, other_driver):
            ext.app.object_registry.add(uav)

        lps = lps_ext._lps_registry.find_by_id(LOCO_LPS_ID)
        assert isinstance(lps, LocoLocalPositioningSystem)
        assert list(lps._uav_getter()) == [connected]
        assert driver.loco is lps
