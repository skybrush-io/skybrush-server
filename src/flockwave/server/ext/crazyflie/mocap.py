from typing import TYPE_CHECKING

from aiocflib.crtp.crtpstack import CRTPPort
from aiocflib.crazyflie.localization import (
    GenericLocalizationCommand,
    Localization,
    LocalizationChannel,
)
from aiocflib.utils.quaternion import QuaternionXYZW

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

    send_pose: bool
    """Whether to send full pose information if it is available."""

    def __init__(
        self,
        driver: "CrazyflieDriver",
        broadcaster: BroadcasterFunction,
        *,
        send_pose: bool = True
    ):
        """Constructor."""
        self._broadcaster = broadcaster
        self._driver = driver
        self.send_pose = bool(send_pose)

    def notify_frame(self, frame: "MotionCaptureFrame") -> None:
        # In theory, the signal that calls this function is rate-limited by the
        # mocap extension, so this function is not called "too often" -- that's
        # why there's no rate limiting here. Add rate limiting if this becomes
        # a problem.

        # TODO(ntamas): broadcast only those that match the address space of
        # the broadcaster; the current solution does not work for multiple
        # Crazyradios in different address spaces yet

        positions: list[tuple[int, tuple[float, float, float]]] = []
        poses: list[tuple[int, tuple[float, float, float], QuaternionXYZW]] = []

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

            if item.position is None:
                # No position, we cannot do anything with this item
                continue

            if item.attitude is None or not self.send_pose:
                # This item only has position information but no attitude
                positions.append((numeric_id, item.position))
            else:
                # This item has both position and attitude info, but we need to
                # convert the attitude to a QuaternionXYZW
                w, x, y, z = item.attitude
                poses.append((numeric_id, item.position, QuaternionXYZW(x, y, z, w)))

        # Crazyflie broadcast localization packets can accommodate coordinates
        # for 4 drones when we send only position information
        for chunk in chunks(positions, 4):
            packet = Localization.encode_external_position_packed(chunk)
            self._broadcaster(
                CRTPPort.LOCALIZATION,
                LocalizationChannel.POSITION_PACKED,
                packet,
            )

        # Crazyflie broadcast localization packets can accommodate coordinates
        # for 2 drones when pose is also needed
        for chunk in chunks(poses, 2):
            packet = bytes(
                [GenericLocalizationCommand.EXT_POSE_PACKED]
            ) + Localization.encode_external_pose_packed(chunk)
            self._broadcaster(
                CRTPPort.LOCALIZATION,
                LocalizationChannel.GENERIC,
                packet,
            )
