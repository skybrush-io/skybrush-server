from async_generator import aclosing
from time import monotonic, time
from trio import sleep
from trio_util import RepeatedEvent
from typing import AsyncIterator, List, Optional, TYPE_CHECKING

from .frame import MotionCaptureFrame, MotionCaptureFrameItem
from .mapping import NameRemapping

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


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
        name_remapping = NameRemapping.from_configuration(
            configuration.get("mapping", {})
        )
        async with aclosing(limiter.iter_frames()) as gen:
            async for frame in gen:
                matched_items: List[MotionCaptureFrameItem] = []
                for item in frame.items:
                    remapped_name = name_remapping(item.name)
                    if remapped_name is not None:
                        item.name = remapped_name
                        matched_items.append(item)

                frame.items = matched_items
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


exports = {"create_frame": create_frame, "enqueue_frame": enqueue_frame}
