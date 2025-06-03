from typing import Dict, Optional

from flockwave.gps.vectors import FlatEarthToGPSCoordinateTransformation

from .trajectory import TrajectorySpecification

__all__ = (
    "get_altitude_reference_from_show_specification",
    "get_coordinate_system_from_show_specification",
    "get_drone_count_from_show_specification",
    "get_group_index_from_show_specification",
    "get_home_position_from_show_specification",
    "get_trajectory_from_show_specification",
    "is_coordinate_system_in_show_specification_geodetic",
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
        raise RuntimeError(
            "Invalid or missing coordinate system specification"
        ) from None


def get_drone_count_from_show_specification(show: ShowSpecification) -> Optional[int]:
    """Returns the number of drones in the show from the show specification if
    known, `None` if not known (which may happen with older versions of
    Skybrush Live that do not send this information yet).
    """
    mission_info = show.get("mission")
    if not mission_info or not isinstance(mission_info, dict):
        return None

    num_drones = mission_info.get("numDrones")
    if num_drones is None:
        return None

    return int(num_drones)


def get_home_position_from_show_specification(
    show: ShowSpecification,
) -> Optional[tuple[float, float, float]]:
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
    relative coordinates (altitude above home level).
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


def is_coordinate_system_in_show_specification_geodetic(
    show: ShowSpecification,
) -> bool:
    """Check if the coordinate system stored in the show specification is
    geodetic."""
    coordinate_system = show.get("coordinateSystem")
    if isinstance(coordinate_system, str) and coordinate_system == "geodetic":
        return True

    return False
