"""Geofence handling extension for the Crazyflie with our custom firmware."""

from aiocflib.crazyflie.mem import write_with_checksum
from aiocflib.crazyflie import Crazyflie
from struct import Struct
from typing import Optional, Sequence

from .crtp_extensions import (
    DRONE_SHOW_PORT,
    MEM_TYPE_FENCE,
    DroneShowCommand,
    FenceLocation,
    FenceType,
)


class Fence:
    """Fence handling for a Crazyflie drone."""

    _crazyflie: Crazyflie
    _is_supported: Optional[bool]

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we are handling fence-related
                commands
        """
        self._crazyflie = crazyflie
        self._is_supported = None

    async def is_enabled(self, fetch: bool = False) -> bool:
        """Returns whether the fence of the Crazyflie is active."""
        value = await self._crazyflie.param.get("fence.enabled", fetch=fetch)
        return bool(value)

    async def is_supported(self) -> bool:
        """Returns whether the Crazyflie supports the fence functionality."""
        if self._is_supported is None:
            try:
                await self._crazyflie.mem.find(MEM_TYPE_FENCE)  # type: ignore
                self._is_supported = True
            except ValueError:
                self._is_supported = False

        return bool(self._is_supported)

    async def set_enabled(self, enabled: bool = True) -> None:
        """Enables or disables the geofence on the Crazyflie.

        Note that this function does not set the type or bounds of the geofence,
        it simply activates or deactivates the one that is already defined. The
        default geofence on a Crazyflie after boot is an infinite one.
        """
        await self._crazyflie.param.set("fence.enabled", 1 if enabled else 0)

    async def set_axis_aligned_bounding_box(
        self, first: Sequence[float], second: Sequence[float]
    ) -> None:
        """Creates and enables a geofence from an axis-aligned bounding box where
        the coordinates of the two corners are given in the parameters.

        Parameters:
            first: the first corner of the bounding box
            second: the second corner of the bounding box
        """
        mins = tuple(min(p, q) for p, q in zip(first, second))
        maxs = tuple(max(p, q) for p, q in zip(first, second))
        if len(mins) != 3 or len(maxs) != 3:
            raise RuntimeError("axis-aligned bounding boxes must be three-dimensional")

        cf = self._crazyflie

        try:
            memory = await cf.mem.find(MEM_TYPE_FENCE)  # type: ignore
        except ValueError:
            raise RuntimeError("Fences are not supported on this drone")

        data = Struct("<Bffffff").pack(
            FenceType.AXIS_ALIGNED_BOUNDING_BOX,
            mins[0],
            mins[1],
            mins[2],
            maxs[0],
            maxs[1],
            maxs[2],
        )
        addr = await write_with_checksum(memory, 0, data, only_if_changed=True)

        await self._crazyflie.run_command(
            port=DRONE_SHOW_PORT,
            command=DroneShowCommand.DEFINE_FENCE,
            data=Struct("<BII").pack(
                FenceLocation.MEM,
                addr,  # address in memory
                len(data),  # length of geofence specification
            ),
        )
        await self.set_enabled(True)
