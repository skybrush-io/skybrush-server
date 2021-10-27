"""Temporary place for functions that are related to the processing of
Skybrush-related trajectories, until we find a better place for them.
"""

from dataclasses import dataclass
from math import ceil, inf
from typing import Dict, Iterable, List, Optional, Tuple

from flockwave.gps.vectors import FlatEarthToGPSCoordinateTransformation

from .utils import BoundingBoxCalculator, Point

__all__ = (
    "get_altitude_reference_from_show_specification",
    "get_coordinate_system_from_show_specification",
    "get_group_index_from_show_specification",
    "get_home_position_from_show_specification",
    "get_trajectory_from_show_specification",
    "TrajectorySpecification",
)


@dataclass
class TrajectorySegment:
    """A single segment in a trajectory specification."""

    t: float
    """The start time of the segment, relative to the takeoff time of the
    trajectory.
    """

    duration: float
    """The total duration of the segment."""

    points: List[Point]
    """The control points of the segment."""

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

        bbox = BoundingBoxCalculator()
        for _, point, control_points in points:
            bbox.add(point)
            for control_point in control_points:
                bbox.add(control_point)

        if margin > 0:
            bbox.pad(margin)

        return bbox.get_corners()

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
                elif dt > max_length:
                    raise ValueError(
                        f"segment starting at t = {t} is too long: {dt} seconds, allowed max is {max_length}"
                    )

                points = [start, *control, point]  # type: ignore
                yield TrajectorySegment(t=prev_t, duration=dt, points=points)

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


def get_trajectory_from_show_specification(
    show: Dict,
) -> TrajectorySpecification:
    """Returns the raw Skybrush trajectory object from the given show
    specification object.
    """
    return TrajectorySpecification(show["trajectory"])


def get_coordinate_system_from_show_specification(
    show: Dict,
) -> FlatEarthToGPSCoordinateTransformation:
    """Returns the coordinate system of the show from the given show
    specification.
    """
    coordinate_system = show.get("coordinateSystem")
    try:
        return FlatEarthToGPSCoordinateTransformation.from_json(coordinate_system)
    except Exception:
        raise RuntimeError("Invalid or missing coordinate system specification")


def get_home_position_from_show_specification(
    show: Dict,
) -> Optional[Tuple[float, float, float]]:
    """Returns the home position of the drone from the given show specification
    object. Units are in meters.
    """
    home = show.get("home")
    if home and len(home) == 3:
        home = tuple(float(x) for x in home)
        return home  # type: ignore
    else:
        return None


def get_altitude_reference_from_show_specification(show: Dict) -> Optional[float]:
    """Returns the altitude above mean sea level where the Z coordinates of the
    show should be referred to, or `None` if the show is to be controlled with
    relative coordinates (altitude above ground level).
    """
    amsl = show.get("amslReference")
    if amsl is None:
        return None
    elif amsl >= -10000 and amsl <= 10000:
        return float(amsl)
    else:
        raise ValueError(f"Invalid altitude reference in show specification: {amsl!r}")


def get_group_index_from_show_specification(show: Dict) -> int:
    """Returns the index of the group of the drone from the given show
    specification object.

    Raises:
        RuntimeError: if the group index is out of its valid bounds. We support
            at most 256 groups, with zero-based indexing
    """
    group_index = int(show["group"]) if "group" in show else 0
    if group_index < 0 or group_index > 255:
        raise RuntimeError("Group index outside valid range")
    return group_index
