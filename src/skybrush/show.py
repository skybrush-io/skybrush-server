from typing import Dict, Optional, Tuple

from flockwave.gps.vectors import FlatEarthToGPSCoordinateTransformation

from .rth_plan import RTHPlan
from .trajectory import TrajectorySpecification

__all__ = (
    "get_altitude_reference_from_show_specification",
    "get_coordinate_system_from_show_specification",
    "get_group_index_from_show_specification",
    "get_home_position_from_show_specification",
    "get_trajectory_from_show_specification",
    "ShowSpecification",
)


ShowSpecification = Dict
"""Type alias for show specification objects."""


def get_trajectory_from_show_specification(
    show: ShowSpecification,
) -> TrajectorySpecification:
    """Returns the raw Skybrush trajectory object from the given show
    specification object.
    """
    return TrajectorySpecification(show["trajectory"])


def get_coordinate_system_from_show_specification(
    show: ShowSpecification,
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
    show: ShowSpecification,
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


def get_altitude_reference_from_show_specification(
    show: ShowSpecification,
) -> Optional[float]:
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


def get_group_index_from_show_specification(show: ShowSpecification) -> int:
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


def get_rth_plan_from_show_specification(show: ShowSpecification) -> Optional[RTHPlan]:
    """Returns the RTH plan from the show specification, or `None` if the show
    specification does not have an RTH plan.
    """
    encoded_plan = show.get("rthPlan")
    return RTHPlan.from_json(encoded_plan) if encoded_plan is not None else None
