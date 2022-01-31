"""Temporary place for functions that are related to the processing of
Skybrush-related trajectories, until we find a better place for them.
"""

from dataclasses import dataclass
from math import ceil, inf
from typing import Dict, Iterable, List, Sequence, Tuple

from .utils import BoundingBoxCalculator, Point

__all__ = ("TrajectorySpecification",)


@dataclass(frozen=True)
class TrajectorySegment:
    """A single segment in a trajectory specification."""

    t: float
    """The start time of the segment, relative to the takeoff time of the
    trajectory.
    """

    duration: float
    """The total duration of the segment."""

    points: List[Point]
    """The control points of the segment, including the start and end point."""

    @property
    def has_control_points(self) -> bool:
        """Returns whether the keypoint has control points."""
        return len(self.points) > 2

    @property
    def start(self) -> Point:
        """Returns the start point of the segment."""
        return self.points[0]

    @property
    def end(self) -> Point:
        """Returns the end point of the segment."""
        return self.points[-1]

    @property
    def start_time(self) -> float:
        """Returns the start time of the segment, relative to the takeoff time."""
        return self.t

    @property
    def end_time(self) -> float:
        """Returns the end time of the segment, relative to the takeoff time."""
        return self.t + self.duration

    def split_at(
        self, fraction: float
    ) -> Tuple["TrajectorySegment", "TrajectorySegment"]:
        """Splits the segment into two pieces at the given relative fraction.

        Parameters:
            fraction: the fraction to split the segment at

        Returns:
            the two smaller pieces that the segment was split into
        """
        if fraction < 0 or fraction > 1:
            raise ValueError("fraction must be between 0 and 1")
        elif fraction == 0:
            first = TrajectorySegment(self.t, 0, [self.start])
            second = self
        elif fraction == 1:
            first = self
            second = TrajectorySegment(self.end_time, 0, [self.end])
        else:
            first_points: List[Point] = []
            second_points: List[Point] = []
            first_points, second_points = self._split_helper(fraction, self.points)
            first_duration = self.duration * fraction
            first = TrajectorySegment(self.t, first_duration, first_points)
            second = TrajectorySegment(
                self.t + first_duration, self.duration - first_duration, second_points
            )

        return first, second

    def split_to_max_duration(
        self, max_duration: float
    ) -> Iterable["TrajectorySegment"]:
        """Splits the segment into smaller pieces such that the duration of
        each piece is less than or equal to the given maxium duration.
        """
        if max_duration <= 0:
            raise ValueError("maximum duration must be positive")

        num_splits = self.duration // max_duration
        current = self
        if num_splits:
            while num_splits > 0:
                ratio = 1 / (num_splits + 1)
                head, current = current.split_at(ratio)
                num_splits -= 1
                yield head
        yield current

    @staticmethod
    def _split_helper(
        t: float, points: Sequence[Point]
    ) -> Tuple[List[Point], List[Point]]:
        """Helper function for splitting the segment at a given fraction.
        See https://pomax.github.io/bezierinfo/#splitting for more details.
        """
        left: List[Point] = []
        right: List[Point] = []

        while True:
            left.append(points[0])
            right.append(points[-1])
            n = len(points)
            if n > 1:
                new_points: List[Point] = []
                for i in range(n - 1):
                    new_points.append(
                        (
                            (1 - t) * points[i][0] + t * points[i + 1][0],
                            (1 - t) * points[i][1] + t * points[i + 1][1],
                            (1 - t) * points[i][2] + t * points[i + 1][2],
                        )
                    )
                points = new_points
            else:
                break

        right.reverse()
        return left, right


class TrajectorySpecification:
    """Class representing a Skybrush trajectory specification received from the
    client during a show upload.
    """

    def __init__(self, data: Dict):
        """Constructor.

        Parameters:
            data: the raw JSON trajectory dictionary in the show specification
        """
        self._data = data

        version = self._data.get("version")
        if version is None:
            raise RuntimeError("trajectory must have a version number")
        if version != 1:
            raise RuntimeError("only version 1 trajectories are supported")

    @property
    def bounding_box(self) -> Tuple[Point, Point]:
        """Returns the coordinates of the opposite corners of the axis-aligned
        bounding box of the trajectory.

        The first point will contain the minimum coordinates, the second will
        contain the maximum coordinates.

        Raises:
            ValueError: if the margin is negative or if the trajectory has no
                points
        """
        return self.get_padded_bounding_box()

    @property
    def is_empty(self) -> bool:
        """Returns whether the trajectory is empty (i.e. has no points)."""
        return not bool(self._data.get("points"))

    @property
    def home_position(self) -> Point:
        """Returns the home position of the drone within the show. Units are
        in meters.
        """
        # TODO(ntamas): I think the 'home' is not here by default but one level
        # higher in the original JSON structure. I think it's time we created a
        # formal specification and stick to it. :-/
        home = self._data.get("home")
        if not home:
            points = self._data.get("points")
            if points:
                _, home, _ = points[0]

        if home and len(home) == 3:
            return float(home[0]), float(home[1]), float(home[2])
        else:
            return 0.0, 0.0, 0.0

    @property
    def landing_height(self) -> float:
        """Returns the height of the last point of the show, in meters.

        TODO(ntamas): this is correct only as long as the trajectory is
        pre-processed when we receive it and the last segment is cut. Fix this
        when we finally migrate to sending the entire trajectory from the client
        to the server.
        """
        height = self._data.get("landingHeight")
        if height is None:
            points = self._data.get("points")
            if points:
                _, last_pos, _ = points[-1]
                height = float(last_pos[2])
            else:
                height = 0.0
        return height

    @property
    def takeoff_time(self) -> float:
        """Returns the takeoff time of the drone within the show, in seconds."""
        return float(self._data.get("takeoffTime", 0.0))

    def get_padded_bounding_box(self, margin: float = 0) -> Tuple[Point, Point]:
        """Returns the coordinates of the opposite corners of the axis-aligned
        bounding box of the trajectory, optionally padded with the given margin.

        The first point will contain the minimum coordinates, the second will
        contain the maximum coordinates.

        Parameters:
            margin: the margin to apply on each side of the bounding box

        Raises:
            ValueError: if the margin is negative or if the trajectory has no
                points
        """
        points = self._data.get("points", [])

        bbox = BoundingBoxCalculator(dim=3)
        for _, point, control_points in points:
            bbox.add(point)
            for control_point in control_points:
                bbox.add(control_point)

        if margin > 0:
            bbox.pad(margin)

        return bbox.get_corners()  # type: ignore

    def iter_segments(self, max_length: float = inf) -> Iterable[TrajectorySegment]:
        points = self._data.get("points")
        if not points:
            return

        prev_t, start = None, None
        for point in points:
            t, point, control = point
            if prev_t is None:
                # This is the first keyframe so we simply make sure that there
                # are no control points
                if control:
                    raise ValueError("first keyframe must have no control points")
            else:
                # We have to be careful and round dt to three digits (i.e.
                # milliseconds, otherwise floating-point errors will slowly
                # accumulate. For instance, if every segment is 0.2 sec, we might
                # drift by ~160 msec over a minute or so)
                dt = round(t - prev_t, 3)
                if dt < 0:
                    raise ValueError(f"time should not move backwards at t = {t}")
                elif dt == 0:
                    raise ValueError(f"time should not stand still at t = {t}")
                else:
                    points = [start, *control, point]  # type: ignore
                    segment = TrajectorySegment(t=prev_t, duration=dt, points=points)
                    if dt > max_length:
                        yield from segment.split_to_max_duration(max_length)
                    else:
                        yield segment

            prev_t = t
            start = point

    def propose_scaling_factor(self) -> int:
        """Proposes a scaling factor to use in a Skybrush binary show file when
        storing the trajectory.
        """
        if self.is_empty:
            return 1

        mins, maxs = self.bounding_box

        coords = []
        coords.extend(abs(x) for x in mins)
        coords.extend(abs(x) for x in maxs)
        extremum = ceil(max(coords) * 1000)

        # With scale=1, we can fit values from 0 to 32767 into the binary show
        # file, so we basically need to divide (extremum+1) by 32768 and round
        # up. This gives us scale = 1 for extrema in [0; 32767],
        # scale = 2 for extrema in [32768; 65535] and so on.
        return ceil((extremum + 1) / 32768)
