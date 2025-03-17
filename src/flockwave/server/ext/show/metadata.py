from typing import Any, Literal, Optional, TypedDict


class ShowCoordinateSystem(TypedDict):
    """The coordinate system of a show."""

    origin: Optional[list[float]]
    """The origin of the coordinate system (longitude, latitude); ``None`` for
   indoor shows.
   """

    orientation: str
    """The orientation of the X axis of the coordinate system, stored as a string
   to avoid rounding errors.
   """

    type: Optional[Literal["nwu", "neu"]]
    """The type of the coordinate system; ``None`` for indoor shows."""


class MissionInfo(TypedDict):
    id: str
    """Unique ID of the mission; may be empty if not provided."""

    title: str
    """The human-readable title of the mission; may be empty if not provided."""

    numDrones: int
    """The number of drones participating in the mission."""


class ShowMetadata(TypedDict):
    """The metadata of a show upload attempt.

    Note the camelCased properties; this is intentional as this has to match
    what is being posted from Skybrush Live.
    """

    coordinateSystem: ShowCoordinateSystem
    """The coordinate system in which the show is defined."""

    geofence: Optional[dict[str, Any]]
    """The geofence of the show."""

    amslReference: Optional[float]
    """The altitude above mean sea level that corresponds to Z=0 in the show;
    ``None`` if the show is controlled based on AGL instead.
    """

    mission: MissionInfo
