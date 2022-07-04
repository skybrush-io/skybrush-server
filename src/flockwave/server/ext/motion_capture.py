"""Extension that provides basic support for motion capture systems.

This extension does not implement support for any _specific_ motion capture
system; it simply provides a common infrastructure for other extensions that
implement support for specific motion capture systems. The extension registers
a signal in the signalling system where motion capture extensions can post
position and attitude information about rigid bodies, which are then mapped
to UAVs. UAV drivers can subscribe to this signal to provide support for
forwarding mocap data to UAVs.
"""

from typing import Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


Position = Tuple[float, float, float]
"""Type alias for 3D position data."""

Attitude = Tuple[float, float, float, float]
"""Type alias for attitude data, expressed as a quaternion in Hamilton
conventions; i.e., the order of items is ``(w, x, y, z)``.
"""

Pose = Tuple[Position, Optional[Attitude]]
"""Type alias for full pose data, which consists of a 3D position of the
rigid body and an optional attitude quaternion, if known.
"""

MotionCaptureFrame = Dict[str, Pose]
"""Type alias for a single frame posted to the ``motion_capture:frame``
signal handler. A frame is a mapping from rigid body names to the corresponding
pose objects.
"""


def load(app: "SkybrushServer"):
    app.import_api("signals").get("motion_capture:frame")


dependencies = ("signals",)
description = "Basic support for motion capture systems"
exports = {}
schema = {}
tags = ("experimental",)
