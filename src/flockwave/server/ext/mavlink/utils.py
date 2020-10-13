from logging import CRITICAL, ERROR, WARNING, INFO, DEBUG
from typing import Optional, List

from .types import MAVLinkMessage

__all__ = (
    "log_id_from_message",
    "log_level_from_severity",
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
