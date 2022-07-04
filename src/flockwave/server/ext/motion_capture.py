"""Extension that provides basic support for motion capture systems.

This extension does not implement support for any _specific_ motion capture
system; it simply provides a common infrastructure for other extensions that
implement support for specific motion capture systems. The extension registers
a signal in the signalling system where motion capture extensions can post
position and attitude information about rigid bodies, which are then mapped
to UAVs. UAV drivers can subscribe to this signal to provide support for
forwarding mocap data to UAVs.
"""

from dataclasses import dataclass
from time import time
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


Position = Tuple[float, float, float]
"""Type alias for 3D position data."""

Attitude = Tuple[float, float, float, float]
"""Type alias for attitude data, expressed as a quaternion in Hamilton
conventions; i.e., the order of items is ``(w, x, y, z)``.
"""

Pose = Tuple[Optional[Position], Optional[Attitude]]
"""Type alias for full pose data, which consists of a 3D position of the
rigid body and an attitude quaternion. The position may be omitted if tracking
was lost for the rigid body. The attitude may also be omitted if it is not known.
"""

MotionCaptureFrameEntry = Tuple[str, Pose]
"""Type alias for an entry in a motion capture frame, containing a name and a
pose object.
"""


@dataclass(frozen=True)
class MotionCaptureFrame:
    """A single frame posted to the ``motion_capture:frame`` signal handler,
    containing a timestamp and an array of rigid body names and pose
    information.
    """

    timestamp: float
    """The timestamp when the frame was obtained."""

    items: List[MotionCaptureFrameEntry]
    """The rigid bodies in this frame, each represented by a name-pose pair."""


def create_frame(timestamp: Optional[float] = None) -> MotionCaptureFrame:
    """Creates a new motion capture frame object with the given timestamp.
    This function must be called by concrete mocap system extensions to create
    a new MotionCaptureFrame_ object.
    """
    if timestamp is None:
        timestamp = time()
    return MotionCaptureFrame(timestamp=timestamp, items=[])


def load(app: "SkybrushServer"):
    app.import_api("signals").get("motion_capture:frame")


dependencies = ("signals",)
description = "Basic support for motion capture systems"
exports = {"create_frame": create_frame}
schema = {}
tags = ("experimental",)
