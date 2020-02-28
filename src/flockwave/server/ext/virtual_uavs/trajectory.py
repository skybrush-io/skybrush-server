"""Functions and classes related to the playback of a pre-programmed
trajectory.
"""

from bisect import bisect
from typing import Any

from flockwave.server.utils import constant

__all__ = ("Trajectory", "TrajectoryPlayer")


#: Type alias for trajectories
Trajectory = Any

#: Zero point to return for empty trajectories
ZERO = [0, 0, 0]


def create_function_for_segment(start, end, control_points=None):
    """Creates a function for a segment that starts at the given point,
    ends at the given other point and has the given set of control points
    in between them.
    """
    if control_points:
        raise RuntimeError("curves not supported yet")

    diff = [e - s for s, e in zip(start, end)]
    coeffs = list(zip(diff, start))

    def func(ratio):
        return [a * ratio + b for a, b in coeffs]

    return func


class TrajectoryPlayer:
    """Trajectory player object that takes a trajectory and is able to tell
    where the drone should be at any given moment in time.
    """

    def __init__(self, trajectory: Trajectory):
        """Constructor.

        Parameters:
            trajectory: the trajectory to play back
        """
        if trajectory.get("version") != 1:
            raise RuntimeError("only version 1 trajectories are supported")

        items = trajectory["points"]

        self._takeoff_time = float(trajectory.get("takeoffTime", 0.0))

        self._num_segments = len(items)
        self._start_times = [segment[0] + self._takeoff_time for segment in items]
        self._segments = [segment[1:] for segment in items]

        self._reset()

    def _reset(self):
        """Resets the state of the trajectory player."""
        self._select_segment(-1)

    def is_before_takeoff(self, time: float):
        """Returns whether the given timestamp is before the takeoff time of
        the mission.
        """
        return time < self._takeoff_time

    def position_at(self, time: float):
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
                next_end = self._start_times[self._segment_index + 1]
                if next_end >= time:
                    # We are done.
                    self._select_segment(self._segment_index + 1)
                    return
            else:
                # Reached the end of the trajectory
                self._select_segment(self._num_segments)

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
            self._current_segment_start_time = -float("inf")
            self._current_segment_length = 0
            if self._num_segments > 0:
                self._current_segment_end_time = self._start_times[0]
                self._current_segment_func = constant(self._segments[0][0])
            else:
                self._current_segment_end_time = float("inf")
                self._current_segment_func = constant(ZERO)
        elif index >= self._num_segments:
            self._current_segment = None
            self._current_segment_length = 0
            self._current_segment_end_time = float("inf")
            if self._num_segments > 0:
                self._current_segment_start_time = self._start_times[-1]
                self._current_segment_func = constant(self._segments[-1][0])
            else:
                self._current_segment_start_time = -float("inf")
                self._current_segment_func = constant(ZERO)
        else:
            self._current_segment = self._segments[index]
            self._current_segment_start_time = self._start_times[index]
            if index < self._num_segments - 1:
                self._current_segment_end_time = self._start_times[index + 1]
                self._current_segment_length = (
                    self._current_segment_end_time - self._current_segment_start_time
                )
                self._current_segment_func = create_function_for_segment(
                    start=self._current_segment[0],
                    end=self._segments[index + 1][0],
                    control_points=self._current_segment[1],
                )
            else:
                self._current_segment_end_time = float("inf")
                self._current_segment_length = 0
                self._current_segment_func = constant(self._current_segment[0])


def test():
    test_data = {
        "version": 1,
        "points": [
            [19, [-2.5, 10, 15], []],
            [19.5, [-2.5, 10, 15], []],
            [20, [-2.27, 10.17, 15.17], []],
            [20.5, [-1.34, 10.85, 15.85], []],
            [21, [0.02, 11.86, 16.86], []],
            [21.5, [1.41, 12.88, 17.88], []],
            [22, [2.79, 13.9, 18.9], []],
        ],
        "takeoffTime": 3,
    }

    player = TrajectoryPlayer(test_data)
    for t in (
        18,
        21,
        21.5,
        22,
        22.2,
        22.4,
        22.6,
        22.8,
        22.999,
        23,
        24.2,
        24.7,
        23,
        18,
        25,
        28,
    ):
        print(t, " ".join(str(x) for x in player.position_at(t)))


if __name__ == "__main__":
    test()
