from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from flockwave.gps.rtcm.packets import RTCMV3Packet

if TYPE_CHECKING:
    from flockwave.server.ext.rtk.types import GPSPacket

__all__ = ("RTKConfigurationPresetType",)


# fmt: off
_BASIC_RTCMV3_PACKETS: frozenset[int] = frozenset(
    [
        # Legacy GPS RTK messages
        1001, 1002, 1003, 1004,
        # Antenna position (optionally with height)
        1005, 1006,
        # Legacy GLONASS RTK messages
        1009, 1010, 1011, 1012,
        # MSM messages for various satellite constellations
        *range(1071, 1140),
        # GLONASS code-phase biases
        1230,
    ]
)
"""This set contains the numeric RTCMv3 identifiers of the minimum set of
messages that need to be forwarded to a UAV to allow it to use RTK corrections.
"""
# fmt: on


def _is_basic_rtcm_packet(packet: GPSPacket) -> bool:
    return (
        isinstance(packet, RTCMV3Packet) and packet.packet_type in _BASIC_RTCMV3_PACKETS
    )


class MessageSet(Enum):
    """Types of supported RTCM message sets."""

    BASIC = "basic"
    """Basic RTCM message set that contains only those messages that are needed
    by GNSS receivers to apply RTK corrections.
    """

    FULL = "full"
    """Full RTCM message set that accepts all messages."""

    def accepts(self, packet: GPSPacket) -> bool:
        """Returns whether the message set should accept the given GPS packet.

        A GPS packet will be accepted if it is an RTCM packet and its message
        type matches the one included in the message set.
        """
        if self is MessageSet.BASIC:
            return _is_basic_rtcm_packet(packet)
        else:
            return True

    def contains(self, message_type: int) -> bool:
        """Returns whether the message set contains the given RTCM message type."""
        return True


class RTKConfigurationPresetType(Enum):
    """Type of RTK configuration presets."""

    BUILTIN = "builtin"
    """BUilt-in configuration presets specified in the main configuration file."""

    DYNAMIC = "dynamic"
    """Dynamic configuration presets created by the extension automatically
    based on hardware detection. Presets created for serial ports are of this
    type.
    """

    USER = "user"
    """User-specified presets in a separate configuration file of the extension.
    These presets can be created, modified or removed by the user.
    """
