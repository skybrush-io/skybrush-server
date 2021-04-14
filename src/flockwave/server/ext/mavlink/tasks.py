"""Background tasks related to the MAVLink extension."""

from collections import defaultdict
from time import monotonic
from trio_util import periodic
from typing import Dict, List, Optional

from .driver import MAVLinkUAV
from .enums import MAVMessageType

__all__ = ("check_uavs_alive",)


def _create_state_summary() -> List[Optional[int]]:
    return [None] * 256


async def check_uavs_alive(
    uavs: List[MAVLinkUAV], signal, log, *, delay: float = 0.5, timeout: float = 5
) -> None:
    """Worker task that runs in the background and checks whether we are
    receiving heartbeats for all the UAVs in the given UAV array. Updates the
    `is_connected` flags of the UAVs accordingly.

    Parameters:
        uavs: the list of UAVs to check. THe list may be mutable; it is iterated
            periodically so you can simply add or remove the UAVs you are
            interested in from this list while the worker task is running.
        signal: a signal that interested parties may subscribe to to receive a
            periodic summary of the status of active UAVs. This is typically
            meant for communication with Skybrush Sidekick.
        delay: number of seconds to wait between consecutive checks
        timeout: number of seconds to wait after a heartbeat to consider a UAV
            as disconnected
    """
    state_summaries = defaultdict(
        _create_state_summary
    )  # type: Dict[str, List[Optional[int]]]

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

        if uavs and signal.receivers:
            try:
                send_state_summary_signal(uavs, signal, state_summaries)
            except Exception:
                log.exception("Failed to prepare UAV state summary")


def send_state_summary_signal(
    uavs: List[MAVLinkUAV], signal, summaries: Dict[str, List[Optional[int]]]
):
    """Helper function that creates a status summary for the UAVs in the given
    list, sorted by MAVLink network IDs, and emits the summaries to subscribers
    of the given signal.

    The status summary is a dictionary mapping network IDs (as strings) to lists
    of 256 entries, one for each possible MAVLink system ID in the network
    (although system ID 0 is reserved so it should not appear as a valid MAVLink
    system ID, ever). Each entry may be `None` (meaning that the drone is not
    present in the network) or the _largest_ (most severe) error code for the
    drone with that ID; this may be zero if the drone has no errors. The lists
    are re-used in later invocations so it is imperative that signal handlers
    do _not_ keep a reference to them; they must copy the list if they need it
    later.
    """
    for uav in uavs:
        summary = summaries[uav.network_id]
        if uav.is_connected:
            errors = uav.status.errors
            summary[uav.system_id] = max(errors, default=0)
        else:
            summary[uav.system_id] = None

    for network_id, summary in summaries.items():
        signal.send(network_id, summary=summary)
