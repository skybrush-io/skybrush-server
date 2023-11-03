"""Mission-related data structures and functions for the server."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field, InitVar
from enum import Enum
from typing import Any, Optional, Sequence, TypedDict, Union

from flockwave.server.show import (
    get_geofence_configuration_from_show_specification,
    get_safety_configuration_from_show_specification,
)

from .geofence import GeofenceConfigurationRequest
from .identifiers import default_id_generator
from .safety import SafetyConfigurationRequest


__all__ = (
    # mission items
    "Altitude",
    "AltitudeReference",
    "Heading",
    "HeadingMode",
    "Marker",
    "MissionItem",
    "MissionItemBundle",
    "MissionItemType",
    "PayloadAction",
    # mission commands
    "MissionCommand",
    "MissionCommandBundle",
    "ChangeAltitudeMissionCommand",
    "ChangeHeadingMissionCommand",
    "ChangeSpeedMissionCommand",
    "GoToMissionCommand",
    "LandMissionCommand",
    "MarkerMissionCommand",
    "ReturnToHomeMissionCommand",
    "SetPayloadMissionCommand",
    "SetParameterMissionCommand",
    "TakeoffMissionCommand",
    "UpdateGeofenceMissionCommand",
    "UpdateSafetyMissionCommand",
)

################################################################################
# MISSION ITEMS


@dataclass
class Altitude:
    """Representation of an altitude relative to a reference altitude in [m]."""

    value: float
    """The altitude value in [m]."""

    reference: AltitudeReference
    """The altitude reference."""

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of altitude."""
        return {
            "reference": self.reference.value,
            "value": round(self.value, ndigits=3),
        }


class AltitudeReference(Enum):
    """Altitude references supported by Skybrush."""

    HOME = "home"
    """Altitude reference is the home altitude."""

    MSL = "msl"
    """Altitude reference is mean sea level."""

    # TODO: add ground / terrain


@dataclass
class Heading:
    """Representation of a heading change action."""

    mode: HeadingMode
    """The heading mode to use."""

    value: Optional[float]
    """Optional fixed heading in [deg]."""

    rate: Optional[float] = None
    """Optional heading change rate in [deg/s]."""

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of heading."""
        return {
            "mode": self.mode.value,
            "value": round(self.value or 0, ndigits=1),
        }


class HeadingMode(Enum):
    """Heading modes supported by Skybrush."""

    ABSOLUTE = "absolute"
    """Heading is given as a fixed absolute value."""

    WAYPOINT = "waypoint"
    """Heading is set to track next waypoint."""


class MissionItem(TypedDict):
    """Representation of a mission item in a format that comes directly from
    Skybrush Live.
    """

    id: Optional[str]
    """The (optional) unique identifier of the mission item."""

    type: str
    """The type of the mission item."""

    parameters: Optional[dict[str, Any]]
    """The parameters of the mission item; exact parameters are dependent on
    the type of the item.
    """


class MissionItemBundle(TypedDict):
    """Representation of an ordered collection of mission items submitted from
    Skybrush Live.
    """

    version: int
    """The version number of the bundle; currently it is always 1."""

    name: Optional[str]
    """The name of the mission to upload to the drone."""

    items: list[MissionItem]
    """The list of mission items in the bundle."""


class MissionItemType(Enum):
    """Mission item types supported by Skybrush."""

    CHANGE_ALTITUDE = "changeAltitude"
    """Command to change the altitude."""

    CHANGE_HEADING = "changeHeading"
    """Command to change the heading (yaw) of the UAV."""

    CHANGE_SPEED = "changeSpeed"
    """Command to change the horizontal and/or vertical speed of the UAV."""

    GO_TO = "goTo"
    """Command to go to a desired position in 2D or 3D space."""

    LAND = "land"
    """Command to land the UAV."""

    MARKER = "marker"
    """Marker that notifies the GCS when the given mission item has been reached."""

    RETURN_TO_HOME = "returnToHome"
    """Command to return to home."""

    SET_PAYLOAD = "setPayload"
    """Command to set a given payload to a desired state."""

    SET_PARAMETER = "setParameter"
    """Command to set an arbitrary autopilot parameter to a desired value."""

    TAKEOFF = "takeoff"
    """Command to takeoff."""

    UPDATE_GEOFENCE = "updateGeofence"
    """Command to update geofence settings."""

    UPDATE_SAFETY = "updateSafety"
    """Command to update safety settings."""


class Marker(Enum):
    """Predefined Marker types for the `MARKER` mission item type."""

    MISSION_STARTED = "start"
    """Notify GCS that the net mission has started."""

    MISSION_ENDED = "end"
    """Notify GCS that the net mission has ended."""


class PayloadAction(Enum):
    """Payload action types."""

    TURN_OFF = "off"
    """Turn off the payload."""

    TURN_ON = "on"
    """Turn on the payload."""

    # TODO: add support for these below later on

    # TRIGGER = "trigger"
    # """Trigger the payload once."""

    # TRIGGER_AT_INTERVAL = "interval"
    # """Trigger the payload repeatedly at given time intervals."""

    # TRIGGER_AT_DISTANCE = "distance"
    # """Trigger the payload repeatedly at given distance travelled."""


################################################################################
# MISSION COMMANDS


def _generate_mission_command_from_mission_item(item: MissionItem) -> MissionCommand:
    """Generate a mission command from a mission item.

    Args:
        item: the mission item to parse

    Returns:
        the parsed mission command

    Raises:
        RuntimeError on any parse error

    """
    _validate_mission_item(item)
    type = MissionItemType(item["type"])
    command: Optional[MissionCommand] = None

    if type == MissionItemType.CHANGE_ALTITUDE:
        command = ChangeAltitudeMissionCommand.from_json(item)
    elif type == MissionItemType.CHANGE_HEADING:
        command = ChangeHeadingMissionCommand.from_json(item)
    elif type == MissionItemType.CHANGE_SPEED:
        command = ChangeSpeedMissionCommand.from_json(item)
    elif type == MissionItemType.GO_TO:
        command = GoToMissionCommand.from_json(item)
    elif type == MissionItemType.LAND:
        command = LandMissionCommand.from_json(item)
    elif type == MissionItemType.MARKER:
        command = MarkerMissionCommand.from_json(item)
    elif type == MissionItemType.RETURN_TO_HOME:
        command = ReturnToHomeMissionCommand.from_json(item)
    elif type == MissionItemType.SET_PAYLOAD:
        command = SetPayloadMissionCommand.from_json(item)
    elif type == MissionItemType.SET_PARAMETER:
        command = SetParameterMissionCommand.from_json(item)
    elif type == MissionItemType.TAKEOFF:
        command = TakeoffMissionCommand.from_json(item)
    elif type == MissionItemType.UPDATE_GEOFENCE:
        command = UpdateGeofenceMissionCommand.from_json(item)
    elif type == MissionItemType.UPDATE_SAFETY:
        command = UpdateSafetyMissionCommand.from_json(item)
    else:
        raise RuntimeError(f"Unhandled mission type: {type!r}")

    return command


def _get_altitude_from_parameters(params: dict[str, Any]) -> Optional[Altitude]:
    if "alt" in params:
        # "alt" will be an object with "reference" and "value" as keys
        value_and_reference = params["alt"]
        if (
            not isinstance(value_and_reference, dict)
            or "value" not in value_and_reference
            or "reference" not in value_and_reference
        ):
            raise RuntimeError(
                "altitude must be a dict with keys named 'value' and 'reference'"
            )
    else:
        return None

    value = value_and_reference.get("value")
    reference = value_and_reference.get("reference")

    if not isinstance(value, (int, float)):
        raise RuntimeError("altitude value must be a number")
    try:
        reference = AltitudeReference(reference)
    except ValueError:
        raise RuntimeError(f"altitude reference unknown: {reference!r}") from None

    return Altitude(value=value, reference=reference)


def _get_heading_from_parameters(params: dict[str, Any]) -> Heading:
    if "heading" not in params:
        raise RuntimeError("missing required parameter: 'heading'")
    # "heading" will be an object with "mode" and "value" as keys
    value_and_mode = params["heading"]
    if (
        not isinstance(value_and_mode, dict)
        or "value" not in value_and_mode
        or "mode" not in value_and_mode
    ):
        raise RuntimeError("heading must be a dict with keys named 'value' and 'mode'")

    value = value_and_mode.get("value")
    mode = value_and_mode.get("mode")

    if not isinstance(value, (int, float)):
        raise RuntimeError("heading value must be a number")
    try:
        mode = HeadingMode(mode)
    except ValueError:
        raise RuntimeError(f"heading mode unknown: {mode!r}") from None

    return Heading(value=value, mode=mode)


def _get_latitude_from_parameters(params: dict[str, Any]) -> float:
    lat = params.get("lat")
    if not isinstance(lat, (int, float)):
        raise RuntimeError("latitude must be a number")
    return float(lat)


def _get_longitude_from_parameters(params: dict[str, Any]) -> float:
    lon = params.get("lon")
    if not isinstance(lon, (int, float)):
        raise RuntimeError("longitude must be a number")
    return float(lon)


def _get_marker_from_parameters(params: dict[str, Any]) -> tuple[Marker, float]:
    marker_str = params.get("marker")
    if not isinstance(marker_str, str) or not marker_str:
        raise RuntimeError("marker type must be a valid string")
    if marker_str == "start":
        marker = Marker.MISSION_STARTED
    elif marker_str == "end":
        marker = Marker.MISSION_ENDED
    else:
        raise RuntimeError(f"marker type {marker_str!r} not handled yet")

    ratio = params.get("ratio")
    if not isinstance(ratio, (int, float)):
        raise RuntimeError("ratio must be a number")
    if ratio < 0 or ratio > 1:
        raise RuntimeError("ratio must be between 0 and 1")

    return (marker, ratio)


def _get_payload_action_from_parameters(
    params: dict[str, Any]
) -> tuple[str, PayloadAction]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("payload name must be a valid string")
    action_str = params.get("action")
    if not isinstance(action_str, str) or not action_str:
        raise RuntimeError("payload action must be a valid string")
    if action_str == "on":
        action = PayloadAction.TURN_ON
    elif action_str == "off":
        action = PayloadAction.TURN_OFF
    else:
        raise RuntimeError(f"payload action {action_str!r} not handled yet")

    return (name, action)


def _get_speed_from_parameters(
    params: dict[str, Any]
) -> tuple[Optional[float], Optional[float]]:
    velocity_xy = params.get("velocityXY")
    if velocity_xy is not None and (
        not isinstance(velocity_xy, (int, float)) or velocity_xy <= 0
    ):
        raise RuntimeError("velocityXY must be a positive number")

    velocity_z = params.get("velocityZ")
    if velocity_z is not None and (
        not isinstance(velocity_z, (int, float)) or velocity_z <= 0
    ):
        raise RuntimeError("velocityZ must be a positive number")

    return (velocity_xy, velocity_z)


def _validate_mission_item(
    item: Any,
    expected_type: Optional[MissionItemType] = None,
    expect_params: Optional[bool] = None,
) -> None:
    """Validates a mission item.

    Args:
        item: the mission item to validate
        expected_type: the optional expected mission type
        expect_params: optional parameter that specifies whether we expect
            parameters (True) or we do not care (False or None)

    Raises:
        RuntimeError in case of any validation error

    """
    if not isinstance(item, dict):
        raise RuntimeError("mission item must be a dict")

    # check optional id
    id = item.get("id")
    if id is not None and not isinstance(id, str):
        raise RuntimeError("mission item ID must be a string")

    # check required type
    if "type" not in item:
        raise RuntimeError("mission item must have a type")
    type = item["type"]
    try:
        type = MissionItemType(type)
    except ValueError:
        raise RuntimeError(f"unknown mission item type: {type!r}") from None
    if expected_type is not None and type != expected_type:
        raise RuntimeError(f"Mission type mismatch: {type!r}!={expected_type!r}")

    # check optional parameters
    params = item.get("parameters")
    if params is not None and not isinstance(params, dict):
        raise RuntimeError("parameters must be a dict or None")
    if expect_params and not params:
        raise RuntimeError("parameters must be a valid dict")


@dataclass
class MissionCommand(ABC):
    """Abstract superclass for mission commands."""

    # TODO: use Python 3.10+ and field(kw_only=True) and then default base value
    # can be added instead of explicit id=None arguemnt in child classes
    id: InitVar[Optional[str]]
    """The unique identifier of the mission command. Set it to None to
    initialize with a random string."""

    def __post_init__(self, id: Optional[str]) -> None:
        self.id = default_id_generator() if id is None else id

    @classmethod
    @abstractmethod
    def from_json(cls, obj: MissionItem):
        """Constructs a command configuration from its JSON representation."""
        raise NotImplementedError

    @property
    @abstractmethod
    def json(self) -> MissionItem:
        """Returns the JSON representation of the mission command."""
        raise NotImplementedError

    @property
    @abstractmethod
    def type(self) -> MissionItemType:
        """Returns the type of the mission command."""
        raise NotImplementedError


@dataclass
class MissionCommandBundle:
    """Representation of an ordered collection of mission commands."""

    version: int = 1
    """The version number of the bundle; currently it is always 1."""

    name: Optional[str] = None
    """The name of the mission to upload to the drone."""

    commands: list[MissionCommand] = field(default_factory=list)
    """The list of mission commands in the bundle."""

    def __post_init__(self):
        self.check_validity()

    @classmethod
    def from_json(cls, bundle: MissionItemBundle):
        """Parse a JSON-represented mission item bundle and convert it to a list
        of mission commands.

        Args:
            bundle: the JSON representation of a mission item bundle

        Returns:
            parsed mission commands as a bundle

        Raises:
            RuntimeError on any parse error
        """

        if not isinstance(bundle, dict):
            raise RuntimeError("mission items must be represented in a dict")

        if bundle.get("version") != 1:
            raise RuntimeError("only version 1 mission item bundles are supported")

        items: Sequence[MissionItem] = bundle.get("items", ())
        commands: list[MissionCommand] = []

        for item in items:
            commands.append(_generate_mission_command_from_mission_item(item))

        return cls(
            version=1,
            name=bundle.get("name"),
            commands=commands,
        )

    def check_validity(self):
        """Checks whether command ids are valid.

        Raises:
            RuntimeError if command ids are not unique
        """
        counter = Counter(command.id for command in self.commands)
        if any(value > 1 for value in counter.values()):
            raise RuntimeError("mission item ids are not unique")

    @property
    def json(self) -> MissionItemBundle:
        """Returns JSON representation of mission commmands in mission item
        bundle format.

        Raises:
            RuntimeError if ids of commands are not unique

        """
        self.check_validity()

        return {
            "version": 1,
            "name": self.name,
            "items": [command.json for command in self.commands],
        }


@dataclass
class ChangeAltitudeMissionCommand(MissionCommand):
    """Mission command that instructs the drone to change its altitude."""

    altitude: Altitude
    """The altitude reference and value to set."""

    velocity_z: Optional[float] = None
    """Vertical velocity when changing altitude in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.CHANGE_ALTITUDE, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None
        alt = _get_altitude_from_parameters(params)
        if alt is None:
            raise RuntimeError("missing required parameter: 'alt'")

        return cls(id=id, altitude=alt)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.CHANGE_ALTITUDE.value,
            "parameters": {
                "alt": self.altitude.json,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.CHANGE_ALTITUDE


@dataclass
class ChangeHeadingMissionCommand(MissionCommand):
    """Mission command that instructs the drone to change its heading."""

    heading: Heading
    """The heading mode and value to set."""

    rate: Optional[float] = None
    """The optional angular rate at which heading should be changed in [deg/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.CHANGE_HEADING, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None
        heading = _get_heading_from_parameters(params)

        return cls(id=id, heading=heading)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.CHANGE_HEADING.value,
            "parameters": {
                "heading": self.heading.json,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.CHANGE_HEADING


@dataclass
class ChangeSpeedMissionCommand(MissionCommand):
    """Mission command that instructs the drone to change its horizontal and/or
    vertical speed for the consecutive waypoints"""

    velocity_xy: Optional[float]
    """The horizontal speed to set optionally in [m/s]."""

    velocity_z: Optional[float]
    """The vertical speed to set optionally in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.CHANGE_SPEED, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None
        velocity_xy, velocity_z = _get_speed_from_parameters(params)

        return cls(id=id, velocity_xy=velocity_xy, velocity_z=velocity_z)

    @property
    def json(self) -> MissionItem:
        parameters = {}
        if self.velocity_xy is not None:
            parameters["velocityXY"] = round(self.velocity_xy, ndigits=3)
        if self.velocity_z is not None:
            parameters["velocityZ"] = round(self.velocity_z, ndigits=3)

        return {
            "id": self.id,
            "type": MissionItemType.CHANGE_SPEED.value,
            "parameters": parameters,
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.CHANGE_SPEED


@dataclass
class GoToMissionCommand(MissionCommand):
    """Mission command that instructs the drone to go to a desired location."""

    latitude: float
    """The desired latitude in [deg]."""

    longitude: float
    """The desired longitude in [deg]."""

    altitude: Optional[Altitude]
    """The desired (optional) altitude."""

    velocity_xy: Optional[float] = None
    """Velocity in the XY (horizontal) plane when approaching the waypoint in [m/s]."""

    velocity_z: Optional[float] = None
    """Velocity in the Z (vertical) plane when approaching the waypoint in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.GO_TO, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None
        lat = _get_latitude_from_parameters(params)
        lon = _get_longitude_from_parameters(params)
        alt = _get_altitude_from_parameters(params)

        return cls(id=id, latitude=lat, longitude=lon, altitude=alt)

    @property
    def json(self) -> MissionItem:
        parameters = {
            "lat": round(self.latitude, ndigits=7),
            "lon": round(self.longitude, ndigits=7),
        }
        if self.altitude is not None:
            parameters["alt"] = self.altitude.json

        return {
            "id": self.id,
            "type": MissionItemType.GO_TO.value,
            "parameters": parameters,
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.GO_TO


@dataclass
class LandMissionCommand(MissionCommand):
    """Mission command that instructs the drone to land in place."""

    velocity_z: Optional[float] = None
    """Vertical velocity while landing in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.LAND, expect_params=False
        )
        id = obj.get("id")
        params = obj.get("parameters")
        if params is not None:
            _, velocity_z = _get_speed_from_parameters(params)
        else:
            velocity_z = None

        return cls(id=id, velocity_z=velocity_z)

    @property
    def json(self) -> MissionItem:
        parameters = {}
        if self.velocity_z is not None:
            parameters["velocityZ"] = round(self.velocity_z, ndigits=3)

        return {
            "id": self.id,
            "type": MissionItemType.LAND.value,
            "parameters": parameters,
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.LAND


@dataclass
class MarkerMissionCommand(MissionCommand):
    """Mission command that serves as a predefined notification to the GCS about
    the completion type and completion ratio of a given state in the mission."""

    marker: Marker
    """The type of marker to send."""

    ratio: float
    """total-distance ratio of the net mission at the marker location. Serves
    as a helper to be used with resumed or multi-drone missions."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.MARKER, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None

        marker, ratio = _get_marker_from_parameters(params)

        return cls(id=id, marker=marker, ratio=ratio)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.MARKER.value,
            "parameters": {
                "marker": self.marker.value,
                "ratio": self.ratio,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.MARKER


@dataclass
class ReturnToHomeMissionCommand(MissionCommand):
    """Mission command that instructs the drone to return to its home
    coordinate horizontally.
    """

    velocity_xy: Optional[float] = None
    """Horizontal velocity while returning to home in [m/s]."""

    velocity_z: Optional[float] = None
    """Vertical velocity while returning to home in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.RETURN_TO_HOME, expect_params=False
        )
        id = obj.get("id")

        return cls(id=id)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.RETURN_TO_HOME.value,
            "parameters": {},
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.RETURN_TO_HOME


@dataclass
class SetPayloadMissionCommand(MissionCommand):
    """Mission command that instructs the drone to set the state of one of its
    payloads.
    """

    name: str
    """The name of the payload to act on."""

    action: PayloadAction
    """The action to perform on the payload."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.SET_PAYLOAD, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None

        name, action = _get_payload_action_from_parameters(params)

        return cls(id=id, name=name, action=action)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.SET_PAYLOAD.value,
            "parameters": {
                "name": self.name,
                "action": self.action.value,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.SET_PAYLOAD


@dataclass
class SetParameterMissionCommand(MissionCommand):
    """Mission command that instructs the drone to set an autopilot parameter
    to the given value.
    """

    name: str
    """The name of the parameter to set."""

    value: Union[str, int, float, bool]
    """The value of the parameter to set."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.SET_PARAMETER, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None

        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("parameter name must be a valid string")
        value = params.get("value")
        if not isinstance(value, (str, int, float, bool)):
            raise RuntimeError(
                "parameter value must be present as a string, a number or a bool"
            )

        return cls(id=id, name=name, value=value)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.SET_PARAMETER.value,
            "parameters": {"name": self.name, "value": self.value},
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.SET_PARAMETER


@dataclass
class TakeoffMissionCommand(MissionCommand):
    """Mission command that instructs the drone to take off."""

    altitude: Altitude
    """The altitude reference and value to takeoff to."""

    velocity_z: Optional[float] = None
    """Vertical velocity while taking off in [m/s]."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj, expected_type=MissionItemType.TAKEOFF, expect_params=True
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None
        alt = _get_altitude_from_parameters(params)
        if alt is None:
            raise RuntimeError("missing required parameter: 'alt'")
        _, velocity_z = _get_speed_from_parameters(params)

        return cls(id=id, altitude=alt, velocity_z=velocity_z)

    @property
    def json(self) -> MissionItem:
        parameters = {
            "alt": self.altitude.json,
        }
        if self.velocity_z is not None:
            parameters["velocityZ"] = round(self.velocity_z, ndigits=3)

        return {
            "id": self.id,
            "type": MissionItemType.TAKEOFF.value,
            "parameters": parameters,
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.TAKEOFF


@dataclass
class UpdateGeofenceMissionCommand(MissionCommand):
    """Mission command that updates geofence settings for the drone."""

    geofence: GeofenceConfigurationRequest
    """Geofence related configuration object."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj,
            expected_type=MissionItemType.UPDATE_GEOFENCE,
            expect_params=True,
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None

        # we need a "geofence" and a "coordinateSystem" entry, where the latter
        # can be "geodetic" or a complete JSON representation of a
        # FlatEarthToGPSCoordinateTransformation
        geofence = get_geofence_configuration_from_show_specification(params)

        return cls(id=id, geofence=geofence)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.UPDATE_GEOFENCE.value,
            "parameters": {
                "geofence": self.geofence.json,
                "coordinateSystem": "geodetic",
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.UPDATE_GEOFENCE


@dataclass
class UpdateSafetyMissionCommand(MissionCommand):
    """Mission command that updates safety settings for the drone."""

    safety: SafetyConfigurationRequest
    """Safety related configuration object."""

    @classmethod
    def from_json(cls, obj: MissionItem):
        _validate_mission_item(
            obj,
            expected_type=MissionItemType.UPDATE_SAFETY,
            expect_params=True,
        )
        id = obj.get("id")
        params = obj["parameters"]
        assert params is not None

        # we need a "safety" entry
        safety = get_safety_configuration_from_show_specification(params)

        return cls(id=id, safety=safety)

    @property
    def json(self) -> MissionItem:
        return {
            "id": self.id,
            "type": MissionItemType.UPDATE_SAFETY.value,
            "parameters": {
                "safety": self.safety.json,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.UPDATE_SAFETY
