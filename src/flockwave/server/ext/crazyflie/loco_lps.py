"""Support for the Loco positioning system of Crazyflie drones as a local
positioning system of the server.

Only the TDoA3 mode of the Loco positioning system is supported. In this mode,
each anchor stores its own position and broadcasts it in every packet that it
sends. The drones are passive listeners that learn the positions of the
anchors from these packets. The server does not keep its own copy of the
positions. It reads them from the first drone with a Loco positioning deck
that connects, and again from any connected drone with a Loco positioning
deck when the configuration is requested or changed.

New anchor positions are sent to the anchors through one of the connected
drones. The anchors do not acknowledge the new positions. Instead, the server
reads the positions back from the same drone and sends the positions that did
not change again, up to a given number of attempts. The other drones learn
the new positions from the anchors on their own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from logging import Logger
from struct import Struct
from typing import TYPE_CHECKING, Any, Protocol

from aiocflib.crazyflie.loco import Vector3D
from flockwave.gps.vectors import PositionXYZ
from flockwave.spec.errors import FlockwaveErrorCode
from trio import Lock, sleep

from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.lps.model import (
    Anchor,
    LocalPositioningSystem,
    LocalPositioningSystemType,
)

if TYPE_CHECKING:
    from aiocflib.crazyflie import Crazyflie

__all__ = (
    "LOCO_LPS_ID",
    "LOCO_LPS_TYPE_ID",
    "LocoConfigurationJSON",
    "LocoLocalPositioningSystem",
    "LocoLocalPositioningSystemType",
    "LocoUAV",
    "read_loco_anchor_positions",
    "write_loco_anchor_position",
)


LOCO_LPS_ID = "loco"
"""ID of the Loco local positioning system instance that the extension
registers.
"""

LOCO_LPS_TYPE_ID = "crazyflie-loco"
"""ID of the type of the Loco local positioning system."""

WRITE_ATTEMPT_COUNT = 3
"""Maximum number of times the new position of an anchor is sent before the
server gives up on the anchor.
"""

VERIFY_DELAY = 1.0
"""Number of seconds to wait after sending new positions to the anchors before
reading them back from the drone that relayed them. The drone needs to receive
at least one packet from each anchor with its new position.
"""


LocoConfigurationJSON = dict[str, Vector3D]
"""JSON representation of the positions of the anchors of a Loco positioning
system, in metres, keyed by anchor ID.
"""


class LocoUAV(Protocol):
    """Interface specification of the UAVs that the Loco local positioning
    system can work with.
    """

    @property
    def id(self) -> str: ...

    @property
    def is_idle_on_ground(self) -> bool:
        """Whether the UAV is on the ground with its motors stopped. The
        positions of the anchors are changed only if all the UAVs are in this
        state.
        """
        ...

    async def get_loco_anchor_positions(self) -> dict[int, Vector3D | None]:
        """Reads the positions of the anchors that the UAV has heard from.

        Raises:
            NotSupportedError: if the UAV has no Loco positioning deck
        """
        ...

    async def set_loco_anchor_position(
        self, anchor_id: int, position: Vector3D
    ) -> bool: ...


class LocoLocalPositioningSystem(LocalPositioningSystem[LocoConfigurationJSON]):
    """Local positioning system that represents the Loco anchors used by
    Crazyflie drones.
    """

    log: Logger | None
    """Logger that the LPS uses to report problems with individual drones."""

    _positions: dict[int, Vector3D | None] | None
    """Positions of the anchors that a drone reported most recently, keyed by
    anchor ID; ``None`` if the positions were not read successfully from any
    drone yet. The position of an anchor is ``None`` if the drone has heard
    from the anchor but does not know its position.
    """

    _read_lock: Lock
    """Lock that prevents UAVs that connect at the same time from all reading
    the positions of the anchors.
    """

    _uav_getter: Callable[[], Iterable[LocoUAV]]
    """Function that returns the UAVs that are currently connected."""

    def __init__(
        self,
        uav_getter: Callable[[], Iterable[LocoUAV]],
        log: Logger | None = None,
    ) -> None:
        """Constructor.

        Parameters:
            uav_getter: function that returns the UAVs that are currently
                connected. The LPS reads and writes the positions of the
                anchors through these UAVs.
            log: logger that the LPS uses to report problems with individual
                drones
        """
        super().__init__()
        self.name = "Loco"
        self.log = log
        self._positions = None
        self._read_lock = Lock()
        self._uav_getter = uav_getter

    async def get_configuration(self) -> LocoConfigurationJSON:
        """Returns the positions of the anchors, as reported by one of the
        connected drones with a Loco positioning deck.

        The positions reported most recently are returned if no such drone is
        connected.
        """
        await self._read_positions(list(self._uav_getter()))

        return {
            str(anchor_id): position
            for anchor_id, position in sorted((self._positions or {}).items())
            if position is not None
        }

    async def handle_uav_connected(self, uav: LocoUAV) -> None:
        """Reads the positions of the anchors from the given UAV that has just
        connected, unless they were already read successfully from another UAV.

        It is meant to be called once for every new connection to a UAV.
        """
        async with self._read_lock:
            if self._positions is None:
                await self._read_positions([uav])

    async def _configure_inner(self, cfg: LocoConfigurationJSON) -> None:
        positions = {int(anchor_id): (x, y, z) for anchor_id, (x, y, z) in cfg.items()}
        if not positions:
            return

        uavs = list(self._uav_getter())
        busy = sorted(uav.id for uav in uavs if not uav.is_idle_on_ground)
        if busy:
            raise RuntimeError(
                "Anchor positions can be changed only when all drones are on the "
                f"ground with their motors stopped. Drones not ready: "
                f"{', '.join(busy)}"
            )

        relay = await self._read_positions(uavs)
        if relay is None:
            raise RuntimeError(
                "Anchor positions can be changed only when at least one drone "
                "with a Loco positioning deck is connected"
            )

        for _ in range(WRITE_ATTEMPT_COUNT):
            pending = self._get_unconfirmed_anchors(positions)
            if not pending:
                return

            for anchor_id in pending:
                await relay.set_loco_anchor_position(anchor_id, positions[anchor_id])

            await sleep(VERIFY_DELAY)
            self._set_positions(await relay.get_loco_anchor_positions())

        pending = self._get_unconfirmed_anchors(positions)
        if pending:
            raise RuntimeError(
                f"Anchors did not confirm their new positions: "
                f"{', '.join(str(anchor_id) for anchor_id in pending)}"
            )

    def _get_unconfirmed_anchors(self, positions: Mapping[int, Vector3D]) -> list[int]:
        """Returns the IDs of the anchors whose positions differ from the
        given positions, in increasing order.
        """
        return [
            anchor_id
            for anchor_id, position in sorted(positions.items())
            if not _are_positions_equal(
                (self._positions or {}).get(anchor_id), position
            )
        ]

    async def _read_positions(self, uavs: Iterable[LocoUAV]) -> LocoUAV | None:
        """Reads the positions of the anchors from the first UAV in the given
        list that has a Loco positioning deck and responds.

        Returns:
            the UAV that reported the positions, or ``None`` if none of the
            UAVs could report them
        """
        for uav in uavs:
            try:
                positions = await uav.get_loco_anchor_positions()
            except NotSupportedError:
                # Drone has no Loco positioning deck
                continue
            except Exception as ex:
                if self.log:
                    self.log.warning(
                        f"Failed to read Loco anchors: {ex}", extra={"id": uav.id}
                    )
                continue
            else:
                self._set_positions(positions)
                return uav

        return None

    def _set_positions(self, positions: Mapping[int, Vector3D | None]) -> None:
        """Replaces the positions of the anchors with the ones reported by a
        UAV, and notifies listeners if anything changed.
        """
        positions = dict(positions)
        if positions == self._positions:
            return

        self._positions = positions
        self.anchors = [
            _create_anchor(anchor_id, position)
            for anchor_id, position in sorted(positions.items())
        ]
        self.notify_updated()


class LocoLocalPositioningSystemType(
    LocalPositioningSystemType[LocoLocalPositioningSystem]
):
    """Type of the Loco local positioning system of Crazyflie drones."""

    _log: Logger | None
    _uav_getter: Callable[[], Iterable[LocoUAV]]

    def __init__(
        self,
        uav_getter: Callable[[], Iterable[LocoUAV]],
        log: Logger | None = None,
    ) -> None:
        """Constructor.

        Parameters:
            uav_getter: function that returns the UAVs that are currently
                connected
            log: logger that the created instances use to report problems with
                individual drones
        """
        self._log = log
        self._uav_getter = uav_getter

    @property
    def description(self) -> str:
        return "Loco positioning system of Crazyflie drones"

    @property
    def name(self) -> str:
        return "Loco"

    def create(self) -> LocoLocalPositioningSystem:
        return LocoLocalPositioningSystem(self._uav_getter, self._log)

    def get_configuration_schema(self) -> dict[str, Any]:
        return CONFIGURATION_SCHEMA


async def read_loco_anchor_positions(cf: Crazyflie) -> dict[int, Vector3D | None]:
    """Reads the positions of all the Loco anchors that a Crazyflie drone has
    heard from.

    Raises:
        NotSupportedError: if the drone does not have a Loco positioning deck
    """
    try:
        return await cf.loco.get_anchor_positions()
    except ValueError:
        # aiocflib throws ValueError if the drone has no Loco memory
        raise NotSupportedError(
            "The drone does not support the Loco positioning system"
        ) from None


async def write_loco_anchor_position(
    cf: Crazyflie, anchor_id: int, position: Vector3D
) -> bool:
    """Asks a Crazyflie drone to send a new position to a Loco anchor.

    Returns:
        whether the drone accepted the packet for sending
    """
    return await cf.loco.set_anchor_position(anchor_id, position)


_float32_vector = Struct("<fff")


def _are_positions_equal(reported: Vector3D | None, expected: Vector3D) -> bool:
    """Returns whether a position reported by a drone is equal to the expected
    position. The positions are compared in the single-precision format that
    the anchors use.
    """
    return reported is not None and _float32_vector.pack(
        *reported
    ) == _float32_vector.pack(*expected)


def _create_anchor(anchor_id: int, position: Vector3D | None) -> Anchor:
    """Creates an anchor with the given ID and position."""
    anchor = Anchor(id=str(anchor_id))
    if position is None:
        anchor.active = False
        anchor.errors.ensure(FlockwaveErrorCode.CONFIGURATION_ERROR)
    else:
        anchor.position = PositionXYZ(*position)
    return anchor


_ANCHOR_ID_PATTERN = "^(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$"

CONFIGURATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Anchor positions",
    "patternProperties": {
        _ANCHOR_ID_PATTERN: {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "additionalProperties": False,
}
"""JSON schema of the configuration of the Loco local positioning system."""
