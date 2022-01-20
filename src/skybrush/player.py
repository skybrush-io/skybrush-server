"""Functions and classes related to the playback of a pre-programmed
trajectory.
"""

from bisect import bisect
from math import inf
from typing import Callable, List, Optional

from flockwave.server.utils import constant

from .trajectory import TrajectorySegment, TrajectorySpecification
from .utils import Point

__all__ = ("TrajectoryPlayer",)


#: Type alias for
#: Zero point to return for empty trajectories
ZERO = (0.0, 0.0, 0.0)


def create_function_for_segment(segment: TrajectorySegment) -> Callable[[float], Point]:
    """Creates a function for a trajectory segment that evaluates it at any
    given _ratio_ of the segment.

    Parameters:
        segment: the segment to evaluate

    Returns:
        a function that can be called with a ratio (between 0 and 1, inclusive)
        and returns the corresponding point on the segment
    """
    if segment.has_control_points:
        return _create_bezier_function_for_segment(segment)
    else:
        return _create_linear_interpolation_function(segment.start, segment.end)


def _create_bezier_function_for_segment(
    segment: TrajectorySegment,
) -> Callable[[float], Point]:
    coords = list(segment.points)

    def de_casteljau_step(start: int, length: int, ratio: float):
        if length <= 0:
            return coords[start]
        else:
            # Make sure to copy here so we don't alter coords
            p = [(1 - ratio) * x for x in de_casteljau_step(start, length - 1, ratio)]
            for i, x in enumerate(de_casteljau_step(start + 1, length - 1, ratio)):
                p[i] += ratio * x
            return p

    def func(ratio: float) -> Point:
        if ratio == 0:
            return tuple(coords[0])  # type: ignore
        elif ratio == 1:
            return tuple(coords[-1])  # type: ignore
        else:
            return tuple(de_casteljau_step(0, len(coords) - 1, ratio))  # type: ignore

    return func


def _create_linear_interpolation_function(
    start: Point, end: Point
) -> Callable[[float], Point]:
    diff = [e - s for s, e in zip(start, end)]
    coeffs = list(zip(diff, start))

    def func(ratio: float) -> Point:
        return tuple(a * ratio + b for a, b in coeffs)  # type: ignore

    return func


class TrajectoryPlayer:
    """Trajectory player object that takes a trajectory and is able to tell
    where the drone should be at any given moment in time.
    """

    _current_segment: Optional[TrajectorySegment]
    _current_segment_start_time: float
    _current_segment_end_time: float
    _current_segment_length: float

    _segments: List[TrajectorySegment]
    _start_times: List[float]
    _takeoff_time: float
    _trajectory: TrajectorySpecification

    def __init__(self, trajectory: TrajectorySpecification):
        """Constructor.

        Parameters:
            trajectory: the trajectory specification to play back
        """
        self._trajectory = trajectory

        self._takeoff_time = self._trajectory.takeoff_time

        self._segments = list(self._trajectory.iter_segments())
        self._num_segments = len(self._segments)
        self._start_times = [
            segment.start_time + self._takeoff_time for segment in self._segments
        ]
        if self._segments:
            self._start_times.append(self._segments[-1].end_time + self._takeoff_time)

        self._reset()

    def _reset(self) -> None:
        """Resets the state of the trajectory player."""
        self._select_segment(-1)

    @property
    def ended(self) -> bool:
        """Returns whether the trajectory has ended, i.e. we have seeked to a
        position that is after the end of the last segment.
        """
        return self._segment_index >= self._num_segments

    def is_before_takeoff(self, time: float) -> bool:
        """Returns whether the given timestamp is before the takeoff time of
        the mission.
        """
        return time < self._takeoff_time

    def position_at(self, time: float) -> Point:
        """Returns the position where the drone should be at the given timestamp
        when flying the trajectory.

        Parameters:
            time: the timestamp
        """
        self._seek_to(time)

        if self._current_segment_length > 0:
            ratio = (
                time - self._current_segment_start_time
            ) / self._current_segment_length
        else:
            # This branch is used for time instants after the last segment
            ratio = 0

        return self._current_segment_func(ratio)

    def _seek_to(self, time: float) -> None:
        """Updates the state variables of the current trajectory if needed to
        ensure that its current segment includes the given time.
        """
        if time >= self._current_segment_start_time:
            if time <= self._current_segment_end_time:
                # We are done.
                return
            if self._segment_index < self._num_segments - 1:
                # Maybe we only need to step to the next segment? This is the
                # common case
                next_end = self._start_times[self._segment_index + 2]
                if next_end >= time:
                    # We are done.
                    self._select_segment(self._segment_index + 1)
                    return
            else:
                # Reached the end of the trajectory
                self._select_segment(self._num_segments)
                return

        # Do things the hard way, with binary search.
        index = bisect(self._start_times, time)
        self._select_segment(index - 1)

    def _select_segment(self, index: int) -> None:
        """Updates the state variables of the current trajectory if needed to
        ensure that the segmet with the given index is the one that is currently
        selected.
        """
        self._segment_index = index

        if index < 0:
            self._current_segment = None
            self._current_segment_start_time = -inf
            self._current_segment_length = 0
            if self._num_segments > 0:
                self._current_segment_end_time = self._start_times[0]
                self._current_segment_func = constant(tuple(self._segments[0].start))
            else:
                self._current_segment_end_time = inf
                self._current_segment_func = constant(ZERO)

        elif index >= self._num_segments:
            self._current_segment = None
            self._current_segment_length = 0
            self._current_segment_end_time = inf
            if self._num_segments > 0:
                self._current_segment_start_time = self._start_times[-1]
                self._current_segment_func = constant(tuple(self._segments[-1].end))
            else:
                self._current_segment_start_time = -inf
                self._current_segment_func = constant(ZERO)

        else:
            self._current_segment = self._segments[index]
            self._current_segment_start_time = self._start_times[index]
            self._current_segment_end_time = self._start_times[index + 1]
            self._current_segment_length = (
                self._current_segment_end_time - self._current_segment_start_time
            )
            self._current_segment_func = create_function_for_segment(
                self._current_segment
            )
