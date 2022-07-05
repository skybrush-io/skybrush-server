from typing import TYPE_CHECKING

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
        pass
