from logging import ERROR, WARNING, INFO, DEBUG
from typing import Optional, List, Union

from flockwave.gps.vectors import GPSCoordinate

from .enums import MAVFrame, MAVParamType
from .types import MAVLinkMessage

__all__ = (
    "decode_param_from_wire_representation",
    "encode_param_to_wire_representation",
    "log_id_from_message",
    "log_level_from_severity",
    "mavlink_nav_command_to_gps_coordinate",
    "mavlink_version_number_to_semver",
)


_severity_to_log_level = [
    ERROR,
    ERROR,
    ERROR,
    ERROR,
    WARNING,
    INFO,
    INFO,
    DEBUG,
]


def decode_param_from_wire_representation(
    value, type: MAVParamType
) -> Union[int, float]:
    """Decodes the given value when it is interpreted as a given MAVLink type,
    received from a MAVLink parameter retrieval command.

    This is a quirk of the MAVLink parameter protocol where the official,
    over-the-wire type of each parameter is a float, but sometimes we want to
    transfer, say 32-bit integers. In this case, the 32-bit integer
    representation is _reinterpreted_ as a float, and the resulting float value
    is sent over the wire; the other side will then _reinterpret_ it again as
    a 32-bit integer.

    See `MAVParamType.decode_float()` for more details and an example.
    """
    return MAVParamType(type).decode_float(value)


def encode_param_to_wire_representation(value, type: MAVParamType) -> float:
    """Encodes the given value as a given MAVLink type, ready to be transferred
    to the remote end encoded as a float.

    This is a quirk of the MAVLink parameter protocol where the official,
    over-the-wire type of each parameter is a float, but sometimes we want to
    transfer, say 32-bit integers. In this case, the 32-bit integer
    representation is _reinterpreted_ as a float, and the resulting float value
    is sent over the wire; the other side will then _reinterpret_ it again as
    a 32-bit integer.

    See `MAVParamType.as_float()` for more details and an example.
    """
    return MAVParamType(type).as_float(value)


def log_id_from_message(
    message: MAVLinkMessage, network_id: Optional[str] = None
) -> str:
    """Returns an identifier composed from the MAVLink system and component ID
    that is suitable for displaying in the logging output.
    """
    system_id, component_id = message.get_srcSystem(), message.get_srcComponent()
    if network_id:
        return f"{network_id}/{system_id:02x}:{component_id:02x}"
    else:
        return f"{system_id:02x}:{component_id:02x}"


def log_level_from_severity(severity: int) -> int:
    """Converts a MAVLink STATUSTEXT message severity (MAVSeverity) into a
    compatible Python log level.
    """
    if severity <= 0:
        return ERROR
    elif severity >= 8:
        return DEBUG
    else:
        return _severity_to_log_level[severity]


def mavlink_nav_command_to_gps_coordinate(message: MAVLinkMessage) -> GPSCoordinate:
    """Creates a GPSCoordinate object from the parameters of a MAVLink
    `MAV_CMD_NAV_...` command typically used in mission descriptions.

    Parameters:
        message: the MAVLink message with fields named `x`, `y` and `z`. It is
            assumed (and not checked) that the message is a MAVLink command
            of type `MAV_CMD_NAV_...`.
    """
    if message.frame in (MAVFrame.GLOBAL, MAVFrame.GLOBAL_INT):
        return GPSCoordinate(lat=message.x / 1e7, lon=message.y / 1e7, amsl=message.z)
    elif message.frame in (
        MAVFrame.GLOBAL_RELATIVE_ALT,
        MAVFrame.GLOBAL_RELATIVE_ALT_INT,
    ):
        return GPSCoordinate(lat=message.x / 1e7, lon=message.y / 1e7, agl=message.z)
    else:
        raise ValueError(f"unknown coordinate frame: {message.frame}")


def mavlink_version_number_to_semver(
    number: int, custom: Optional[List[int]] = None
) -> str:
    """Converts a version number found in the MAVLink `AUTOPILOT_VERSION` message
    to a string representation, in semantic version number format.

    Parameters:
        number: the numeric representation of the version number
        custom: the MAVLink representation of the "custom" component of the
            version number, if known
    """
    major = (number >> 24) & 0xFF
    minor = (number >> 16) & 0xFF
    patch = (number >> 8) & 0xFF
    prerelease = number & 0xFF

    version = [f"{major}.{minor}.{patch}"]

    # prerelease component is interpreted according to how ArduPilot uses it
    official = prerelease == 255
    if prerelease < 64:
        version.append(f"-dev.{prerelease}")
    elif prerelease < 128:
        version.append(f"-alpha.{prerelease - 64}")
    elif prerelease < 192:
        version.append(f"-beta.{prerelease - 128}")
    elif not official:
        version.append(f"-rc.{prerelease - 192}")

    if custom and not official:
        version.append("+" + bytes(custom).rstrip(b"\x00").decode("utf-8", "replace"))

    return "".join(version)
