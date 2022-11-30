"""Mission-related data structures and functions for the server."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict


__all__ = (
    # mission items
    "Altitude",
    "AltitudeReference",
    "Heading",
    "HeadingMode",
    "MissionItem",
    "MissionItemBundle",
    "MissionItemType",
    "PayloadAction",
    # mission commands
    "MissionCommand",
    "ChangeAltitudeMissionCommand",
    "ChangeHeadingMissionCommand",
    "GoToMissionCommand",
    "LandMissionCommand",
    "ReturnToHomeMissionCommand",
    "SetPayloadMissionCommand",
    "TakeoffMissionCommand",
    # functions
    "generate_mission_command_from_mission_item",
    "generate_mission_commands_from_mission_items",
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
    def json(self) -> Dict[str, Any]:
        """Returns a JSON representation of altitude."""
        return {
            "reference": self.reference.value,
            "value": self.value,
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
    def json(self) -> Dict[str, Any]:
        """Returns a JSON representation of heading."""
        return {
            "mode": self.mode.value,
            "value": self.value or 0,
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

    type: str
    """The type of the mission item."""

    parameters: Optional[Dict[str, Any]]
    """The parameters of the mission item; exact parameters are dependent on
    the type of the item.
    """


class MissionItemBundle(TypedDict):
    """Representation of a collection of mission items submitted from Skybrush
    Live.
    """

    version: int
    """The version number of the bundle; currently it is always 1."""

    name: Optional[str]
    """The name of the mission to upload to the drone."""

    items: List[MissionItem]
    """The list of mission items in the bundle."""


class MissionItemType(Enum):
    """Mission item types supported by Skybrush."""

    CHANGE_ALTITUDE = "changeAltitude"
    """Command to change the altitude."""

    CHANGE_HEADING = "changeHeading"
    """Command to change the heading (yaw) of the UAV."""

    GO_TO = "goTo"
    """Command to go to a desired position in 2D or 3D space."""

    LAND = "land"
    """Command to land the UAV."""

    RETURN_TO_HOME = "returnToHome"
    """Command to return to home."""

    SET_PAYLOAD = "setPayload"
    """Command to set a given payload to a desired state."""

    TAKEOFF = "takeoff"
    """Command to takeoff."""


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


def _get_altitude_from_parameters(params: Dict[str, Any]) -> Optional[Altitude]:
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
        raise RuntimeError(f"altitude reference unknown: {reference!r}")

    return Altitude(value=value, reference=reference)


def _get_heading_from_parameters(params: Dict[str, Any]) -> Heading:
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
        raise RuntimeError(f"heading mode unknown: {mode!r}")

    return Heading(value=value, mode=mode)


def _get_latitude_from_parameters(params: Dict[str, Any]) -> float:
    lat = params.get("lat")
    if not isinstance(lat, (int, float)):
        raise RuntimeError("latitude must be a number")
    return float(lat)


def _get_longitude_from_parameters(params: Dict[str, Any]) -> float:
    lon = params.get("lon")
    if not isinstance(lon, (int, float)):
        raise RuntimeError("longitude must be a number")
    return float(lon)


def _get_payload_action_from_parameters(
    params: Dict[str, Any]
) -> Tuple[str, PayloadAction]:
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

    if "type" not in item:
        raise RuntimeError("mission item must have a type")

    type = item["type"]
    try:
        type = MissionItemType(type)
    except ValueError:
        raise RuntimeError(f"unknown mission item type: {type!r}")
    if expected_type is not None and type != expected_type:
        raise RuntimeError(f"Mission type mismatch: {type!r}!={expected_type!r}")

    params = item.get("parameters")
    if params is not None and not isinstance(params, dict):
        raise RuntimeError("parameters must be a dict or None")
    if expect_params and not params:
        raise RuntimeError("parameters must be a valid dict")


class MissionCommand(ABC):
    """Abstract superclass for mission commands."""

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
        params = obj["parameters"]
        assert params is not None
        alt = _get_altitude_from_parameters(params)
        if alt is None:
            raise RuntimeError("missing required parameter: 'alt'")

        return cls(altitude=alt)

    @property
    def json(self) -> MissionItem:
        return {
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
        params = obj["parameters"]
        assert params is not None
        heading = _get_heading_from_parameters(params)

        return cls(heading=heading)

    @property
    def json(self) -> MissionItem:
        return {
            "type": MissionItemType.CHANGE_HEADING.value,
            "parameters": {
                "heading": self.heading.json,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.CHANGE_HEADING


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
        params = obj["parameters"]
        assert params is not None
        lat = _get_latitude_from_parameters(params)
        lon = _get_longitude_from_parameters(params)
        alt = _get_altitude_from_parameters(params)

        return cls(latitude=lat, longitude=lon, altitude=alt)

    @property
    def json(self) -> MissionItem:
        retval = {
            "type": MissionItemType.GO_TO.value,
            "parameters": {
                "lat": self.latitude,
                "lot": self.longitude,
            },
        }
        if self.altitude is not None:
            retval["parameters"]["alt"] = self.altitude.json

        return retval  # type: ignore

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

        return cls()

    @property
    def json(self) -> MissionItem:
        return {"type": MissionItemType.LAND.value, "parameters": {}}

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.LAND


@dataclass
class ReturnToHomeMissionCommand(MissionCommand):
    """Mission command that instructs the drone to return to its home
    coordinate.
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

        return cls()

    @property
    def json(self) -> MissionItem:
        return {"type": MissionItemType.RETURN_TO_HOME.value, "parameters": {}}

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
        params = obj["parameters"]
        assert params is not None

        name, action = _get_payload_action_from_parameters(params)

        return cls(name=name, action=action)

    @property
    def json(self) -> MissionItem:
        return {
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
        params = obj["parameters"]
        assert params is not None
        alt = _get_altitude_from_parameters(params)
        if alt is None:
            raise RuntimeError("missing required parameter: 'alt'")

        return cls(altitude=alt)

    @property
    def json(self) -> MissionItem:
        return {
            "type": MissionItemType.TAKEOFF.value,
            "parameters": {
                "alt": self.altitude.json,
            },
        }

    @property
    def type(self) -> MissionItemType:
        return MissionItemType.TAKEOFF


################################################################################
# FUNCTIONS

# TODO: this below might be more suited in the mission extension


def generate_mission_command_from_mission_item(item: MissionItem) -> MissionCommand:
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
    elif type == MissionItemType.GO_TO:
        command = GoToMissionCommand.from_json(item)
    elif type == MissionItemType.LAND:
        command = LandMissionCommand.from_json(item)
    elif type == MissionItemType.RETURN_TO_HOME:
        command = ReturnToHomeMissionCommand.from_json(item)
    elif type == MissionItemType.SET_PAYLOAD:
        command = SetPayloadMissionCommand.from_json(item)
    elif type == MissionItemType.TAKEOFF:
        command = TakeoffMissionCommand.from_json(item)
    else:
        raise RuntimeError(f"Unhandled mission type: {type!r}")

    return command


def generate_mission_commands_from_mission_items(
    bundle: MissionItemBundle,
) -> List[MissionCommand]:
    """Parse a mission item bundle and convert it to a list of mission commands.

    Args:
        bundle: the JSON representation of a mission item bundle

    Returns:
        list of parsed mission commands

    Raises:
        RuntimeError on any parse error

    """

    if not isinstance(bundle, dict):
        raise RuntimeError("mission items must be represented in a dict")

    if bundle.get("version") != 1:
        raise RuntimeError("only version 1 mission item bundles are supported")

    items: Sequence[MissionItem] = bundle.get("items", ())
    commands: List[MissionCommand] = []

    for item in items:
        commands.append(generate_mission_command_from_mission_item(item))

    return commands
