"""Extension that provides the server with the concept of the physical location
of the server in geodetic coordinates.
"""

from __future__ import annotations

from trio import CancelScope, sleep, sleep_forever
from typing import Any, TYPE_CHECKING, Optional, cast

from flockwave.gps.vectors import GPSCoordinate
from flockwave.logger import Logger
from flockwave.server.ext.base import Extension
from flockwave.server.ext.location import Location
from flockwave.server.model.uav import UAV
from flockwave.server.utils.generic import use

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


DEFAULT_PRIORITY = 0
"""Default priority of locations provided by this extension."""

KEY = "from_uavs"
"""Key to identify locations from this extension."""

MAX_AGE_MSEC = 60000
"""Maximum age of the status information of the UAV, in milliseconds."""


def is_valid_position(position: Optional[GPSCoordinate]) -> bool:
    return (
        position is not None
        and position.lat is not None
        and position.lon is not None
        and (position.lat != 0 or position.lon != 0)
    )


class LocationFromUAVSExtension(Extension):
    """Extension that tracks the location of a UAV and provides it as an
    approximation of the location of the server.
    """

    def _pick_uav_to_track(self) -> Optional[UAV]:
        """Picks a single UAV from the UAV registry of the server whose location
        will be used as a proxy for the location of the server.

        Returns:
            the UAV that was picked or ``None`` if there are no suitable UAVs
            in the registry.
        """
        assert self.app is not None

        for uav_id in self.app.object_registry.ids_by_type(UAV):
            uav = cast(UAV, self.app.object_registry.find_by_id(uav_id))
            status = uav.status
            if status.age_msec < MAX_AGE_MSEC and is_valid_position(status.position):
                return uav

    async def pick_uav_to_track(self) -> UAV:
        """Picks a single UAV from the UAV registry of the server whose location
        will be used as a proxy for the location of the server.

        Blocks until such a UAV has been found. Handles exceptions gracefully.

        Returns:
            the UAV that was picked
        """
        assert self.log is not None

        maybe_uav: Optional[UAV]

        await self.wait_for_at_least_one_uav()

        while True:
            try:
                maybe_uav = self._pick_uav_to_track()
            except Exception:
                maybe_uav = None
                self.log.warn("Error while finding UAV to track, retrying in 1 second")

            if maybe_uav is not None:
                return maybe_uav

            await sleep(1)

    async def run(
        self, app: SkybrushServer, configuration: dict[str, Any], log: Logger
    ):
        while True:
            uav = await self.pick_uav_to_track()
            await self.use_location_of_uav(uav)

    async def use_location_of_uav(self, uav: UAV) -> None:
        """Reports the location of the given UAV as the location of the server,
        then watches the UAV for status information. If the UAV status becomes
        stale, revokes the proposed location.
        """
        assert self.app is not None

        api = self.app.extension_manager.import_api("location")
        location = Location(uav.status.position)
        with use(api.provide_location(KEY, location)):
            while True:
                status = uav.status
                to_sleep = MAX_AGE_MSEC - status.age_msec
                if to_sleep < 0:
                    return

                await sleep(to_sleep)

    async def wait_for_at_least_one_uav(self):
        """Blocks the current task until there is at least one UAV in the
        registry.
        """
        assert self.app is not None

        registry = self.app.object_registry
        while True:
            for _ in registry.ids_by_type(UAV):
                return

            with CancelScope() as scope:

                def on_uav_added(sender, *, object):
                    scope.cancel()

                with registry.added.connected_to(on_uav_added):
                    await sleep_forever()


construct = LocationFromUAVSExtension
dependencies = ("location",)
description = "Infers the physical location of the server from managed UAVs"
schema = {
    "properties": {
        "priority": {
            "title": "Priority",
            "description": "Priority of the location proposed by this extension",
            "type": "number",
            "default": DEFAULT_PRIORITY,
            "required": True,
        }
    }
}
tags = "experimental"
