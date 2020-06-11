"""Background tasks related to the MAVLink extension."""

from time import monotonic
from trio_util import periodic
from typing import List

from .driver import MAVLinkUAV
from .enums import MAVMessageType

__all__ = ("check_uavs_alive",)


async def check_uavs_alive(
    uavs: List[MAVLinkUAV], delay: float = 0.5, timeout: float = 5
) -> None:
    """Worker task that runs in the background and checks whether we are
    receiving heartbeats for all the UAVs in the given UAV array. Updates the
    `is_connected` flags of the UAVs accordingly.

    Parameters:
        delay: number of seconds to wait between consecutive checks
        timeout: number of seconds to wait after a heartbeat to consider a UAV
            as disconnected
    """
    # TODO(ntamas): remove UAVs that have been disconnected for a long while?
    async for _ in periodic(delay):
        now = monotonic()
        for uav in uavs:
            heartbeat_age = uav.get_age_of_message(MAVMessageType.HEARTBEAT, now)
            if uav.is_connected:
                if heartbeat_age >= timeout:
                    uav.notify_disconnection()
            else:
                if heartbeat_age < timeout:
                    uav.notify_reconnection()
