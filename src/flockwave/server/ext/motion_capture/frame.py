from dataclasses import dataclass
from typing import Optional

from .types import Attitude, Position

__all__ = ("MotionCaptureFrame", "MotionCaptureFrameItem")


@dataclass
class MotionCaptureFrameItem:
    """An item in a motion capture frame, containing a name, an optional 3D
    position and an optional attitude quaternion. The position may be omitted if
    tracking was lost for the rigid body. The attitude may also be omitted if
    it is not known. (Typically, the attitude is also omitted if the position
    is omitted).
    """

    name: str
    """The name of the rigid body"""

    position: Optional[Position] = None
    """The position information in the pose data"""

    attitude: Optional[Attitude] = None
    """The attitude information in the pose data"""


@dataclass
class MotionCaptureFrame:
    """A single frame posted to the ``motion_capture:frame`` signal handler,
    containing a timestamp and an array of rigid body names and pose
    information.
    """

    timestamp: float
    """The timestamp when the frame was obtained."""

    items: list[MotionCaptureFrameItem]
    """The rigid bodies in this frame, each represented by a name-pose pair."""

    def add_item(
        self,
        name: str,
        position: Optional[Position] = None,
        attitude: Optional[Attitude] = None,
    ) -> MotionCaptureFrameItem:
        """Adds a new item to this frame and returns the newly added item.

        Parameters:
            name: the name of the rigid body
            position: the position information; ``None`` if tracking was lost
            attitude: the attitude quaternion; ``None`` if tracking was lost or
                it is not tracked
        """
        item = MotionCaptureFrameItem(name=name, position=position, attitude=attitude)
        self.items.append(item)
        return item
