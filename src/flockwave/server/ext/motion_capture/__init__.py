"""Extension that provides basic support for motion capture systems.

This extension does not implement support for any _specific_ motion capture
system; it simply provides a common infrastructure for other extensions that
implement support for specific motion capture systems. The extension registers
a signal in the signalling system where motion capture extensions can post
position and attitude information about rigid bodies, which are then mapped
to UAVs. UAV drivers can subscribe to this signal to provide support for
forwarding mocap data to UAVs.
"""

from .extension import exports, run
from .frame import MotionCaptureFrame, MotionCaptureFrameItem

__all__ = (
    "dependencies",
    "exports",
    "description",
    "run",
    "schema",
    "tags",
    "MotionCaptureFrame",
    "MotionCaptureFrameItem",
)

dependencies = ("signals",)
description = "Basic support for motion capture systems"
schema = {
    "properties": {
        "frame_rate": {
            "type": "number",
            "title": "Frame rate limit",
            "description": (
                "Maximum number of frames that should be forwarded to UAV "
                "drivers. Zero or negative numbers mean no frame limit; otherwise, "
                "the extension ensures that UAV drivers do not receive position "
                "and attitude information more frequently than this threshold."
            ),
            "default": 10,
        }
    }
}
tags = ("experimental",)
