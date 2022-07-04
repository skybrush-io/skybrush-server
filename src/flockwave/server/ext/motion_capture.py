"""Extension that provides basic support for motion capture systems.

This extension does not implement support for any _specific_ motion capture
system; it simply provides a common infrastructure for other extensions that
implement support for specific motion capture systems. The extension registers
a signal in the signalling system where motion capture extensions can post
position and attitude information about rigid bodies, which are then mapped
to UAVs. UAV drivers can subscribe to this signal to provide support for
forwarding mocap data to UAVs.
"""

from async_generator import aclosing
from dataclasses import dataclass
from time import monotonic, time
from trio import sleep
from trio_util import RepeatedEvent
from typing import AsyncIterator, List, Optional, Tuple, TYPE_CHECKING

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


class FrameRateLimiter:
    """Rate limiter for incoming frames that yields a new frame only when a
    given delay has passed since the dispatch of the last frame.
    """

    _delay: Optional[float]
    """Number of seconds to wait between consecutive dispatches; ``None`` if
    no rate limitation should take place.
    """

    _last_frame: MotionCaptureFrame
    """The last frame that was received by the frame rate limiter the last
    time. Currently we assume that there is only one active mocap system and it
    posts data about all rigid bodies in a single frame, so we can always simply
    overwrite an earlier frame with the most recent one without worrying about
    losing information. This restriction may be relaxed in the future.
    """

    _has_new_frame: RepeatedEvent
    """Event that signals to the frame limiter that a new frame was posted."""

    def __init__(self, frame_rate: Optional[float] = None):
        """Constructor."""
        if frame_rate is None:
            self._delay = None
        elif frame_rate > 0:
            self._delay = 1.0 / float(frame_rate)
        else:
            raise RuntimeError(f"Invalid frame rate: {frame_rate!r}")

        self._has_new_frame = RepeatedEvent()
        self._last_frame = MotionCaptureFrame(timestamp=time(), items=[])

    def enqueue_frame(self, frame: MotionCaptureFrame):
        """Enqueues a new frame to be processed and eventually dispatched by the
        rate limiter.
        """
        self._last_frame = frame
        self._has_new_frame.set()

    async def iter_frames(self) -> AsyncIterator[MotionCaptureFrame]:
        """Iterates over the received frames, ensuring that the iterator does
        not yield a new item more frequently than the number of frames per
        second prescribed in the constructor.
        """
        delay = self._delay

        if delay:
            deadline = monotonic() - 2 * delay

            while True:
                await self._has_new_frame.wait()

                time_left = deadline - monotonic()
                if time_left > 0:
                    await sleep(time_left)

                yield self._last_frame
                deadline = monotonic() + delay

        else:
            while True:
                await self._has_new_frame.wait()
                yield self._last_frame


limiter: Optional[FrameRateLimiter] = None
"""Frame rate limiter object that batches incoming motion capture frames to
ensure that UAVs do not receive them faster than a prescribed frequency.
"""


async def run(app: "SkybrushServer", configuration):
    global limiter

    fps_limit: Optional[float] = configuration.get("frame_rate", 10)
    if not isinstance(fps_limit, float) or fps_limit <= 0:
        fps_limit = None

    signal = app.import_api("signals").get("motion_capture:frame")
    try:
        limiter = FrameRateLimiter(fps_limit)
        async with aclosing(limiter.iter_frames()) as gen:
            async for frame in gen:
                signal.send(frame=frame)
    finally:
        limiter = None


def create_frame(timestamp: Optional[float] = None) -> MotionCaptureFrame:
    """Creates a new motion capture frame object with the given timestamp.
    This function must be called by concrete mocap system extensions to create
    a new MotionCaptureFrame_ object.
    """
    if timestamp is None:
        timestamp = time()
    return MotionCaptureFrame(timestamp=timestamp, items=[])


def enqueue_frame(frame: MotionCaptureFrame) -> None:
    """Enqueues the given frame to be dispatched on the signal bus.

    Note that the frame might not be dispatched immediately; if the extension
    is configured to use rate limiting, the frame might simply be stored and
    then dispatched later.
    """
    global limiter

    if limiter is not None:
        limiter.enqueue_frame(frame)


dependencies = ("signals",)
description = "Basic support for motion capture systems"
exports = {"create_frame": create_frame, "enqueue_frame": enqueue_frame}
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
