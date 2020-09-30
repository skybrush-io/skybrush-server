"""Temporary place for functions that are related to the processing of
Skybrush-related trajectories, until we find a better place for them.
"""

from dataclasses import dataclass
from typing import Dict, Generator, Optional, Tuple

__all__ = ("get_skybrush_trajectory_from_show_specification", "TrajectorySpecification")


#: Type specification for a single point in a trajectory
Point = Tuple[float, float, float]


@dataclass
class TrajectorySegment:
    """A single segment in a trajectory specification."""

    t: float
    duration: float
    points: Point

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
    def home_position(self) -> Tuple[float, float, float]:
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

    def segments(self) -> Generator[TrajectorySegment, None, None]:
        points = self._data.get("points")
        if not points:
            return

        prev_t, start = None, None
        for point in points:
            t, point, control = point
            if start is None:
                # This is the first keyframe so we simply make sure that there
                # are no control points
                if control:
                    raise ValueError("first keyframe must have no control points")
            else:
                dt = t - prev_t
                if dt < 0:
                    raise ValueError(f"time should not move backwards at t = {t}")
                elif dt == 0:
                    raise ValueError(f"time should not stand still at t = {t}")
                elif dt > 65.5:
                    raise ValueError(
                        f"segment too long: {dt} seconds, allowed max is 65.5"
                    )
                yield TrajectorySegment(
                    t=prev_t, duration=dt, points=(start, *control, point)
                )

            prev_t = t
            start = point


def get_skybrush_trajectory_from_show_specification(
    show: Dict,
) -> TrajectorySpecification:
    """Returns the raw Skybrush trajectory object from the given show
    specification object.
    """
    return TrajectorySpecification(show["trajectory"])


def get_home_position_from_show_specification(
    show: Dict,
) -> Optional[Tuple[float, float, float]]:
    """Returns the home position of the drone from the given show specification
    object. Units are in meters.
    """
    home = show.get("home")
    if home and len(home) == 3:
        home = [float(x) for x in home]
        return home
    else:
        return None
