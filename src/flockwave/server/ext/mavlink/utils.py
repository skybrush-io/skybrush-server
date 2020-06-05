from logging import CRITICAL, ERROR, WARNING, INFO, DEBUG
from typing import Optional

from .types import MAVLinkMessage

__all__ = ("log_id_from_message",)


_severity_to_log_level = [
    CRITICAL,
    CRITICAL,
    CRITICAL,
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
        return CRITICAL
    elif severity >= 8:
        return DEBUG
    else:
        return _severity_to_log_level[severity]
