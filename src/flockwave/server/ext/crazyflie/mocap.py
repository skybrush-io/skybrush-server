from typing import List, Tuple, TYPE_CHECKING

from aiocflib.crtp.crtpstack import CRTPPort
from aiocflib.crazyflie.localization import Localization, LocalizationChannel

from flockwave.server.utils import chunks

from .connection import BroadcasterFunction

if TYPE_CHECKING:
    from .driver import CrazyflieDriver
    from flockwave.server.ext.motion_capture import MotionCaptureFrame

__all__ = ("CrazyflieMocapFrameHandler",)


class CrazyflieMocapFrameHandler:
    """Handler task that receives frames from mocap systems and dispatches
    the appropriate packets to the corresponding Crazyflie drones.
    """

    _broadcaster: BroadcasterFunction
    _driver: "CrazyflieDriver"

    def __init__(self, driver: "CrazyflieDriver", broadcaster: BroadcasterFunction):
        """Constructor."""
        self._broadcaster = broadcaster
        self._driver = driver

    def notify_frame(self, frame: "MotionCaptureFrame") -> None:
        # TODO(ntamas): in theory, the signal that calls this function is
        # rate-limited by the mocap extension, so this function is not called
        # "too often" -- that's why there's no rate limiting here. Add rate
        # limiting if this becomes a problem

        # uav_ids = [item.name for item in frame.items]
        # address_spaces = self._driver.sort_uav_ids_by_address_spaces(uav_ids)

        # TODO(ntamas): broadcast only those that match the address space of
        # the broadcaster; the current solution does not work for multiple
        # Crazyradios in different address spaces yet

        items: List[Tuple[int, Tuple[float, float, float]]] = []
        for item in frame.items:
            # TODO(ntamas): currently we assume that the numeric ID of the
            # Crazyflie that we need to send in the localization packet is
            # equal to the numeric ID in Skybrush. This is a hack, but we do not
            # have a better mechanism yet; the other option would be to take the
            # Crazyflie URI, assume that it ends with the radio address in hex,
            # and then convert the last two characters back to a numeric ID.
            try:
                numeric_id = int(item.name)
            except ValueError:
                continue
            if item.position is not None:
                items.append((numeric_id, item.position))

        # Crazyflie broadcast localization packets can accommodate coordinates
        # for 4 drones
        for chunk in chunks(items, 4):
            packet = Localization.encode_external_position_packed(chunk)
            self._broadcaster(
                CRTPPort.LOCALIZATION,
                LocalizationChannel.POSITION_PACKED,
                packet,
            )
