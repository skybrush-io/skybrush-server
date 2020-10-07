from dataclasses import dataclass
from struct import Struct

from .types import MAVLinkMessage

__all__ = ("DroneShowStatus",)


@dataclass
class DroneShowStatus:
    """Data class representing a Skybrush-specific drone show status object."""

    light: int = 0

    #: Identifier of Skybrush-specific DATA16 show status packets
    TYPE = 0x5B

    #: Structure of Skybrush-specific DATA16 show status packets
    _struct = Struct("<iHBB")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw body of a MAVLink
        DATA16 packet that has already been truncated to the desired length of
        the packet.
        """
        start_time, light, flags, stage = cls._struct.unpack(data[:8])
        return cls(light=light)

    @classmethod
    def from_mavlink_message(cls, message: MAVLinkMessage):
        """Constructs a DroneShowStatus_ object from a MAVLink DATA16 packet.

        Raises:
            ValueError: if the type of the MAVLink DATA16 packet does not match
                the expected type of a Skybrush-specific show status packet
        """
        if message.type != cls.TYPE:
            raise ValueError(
                f"type of MAVLink packet is {message.type}, expected {cls.TYPE}"
            )

        return cls.from_bytes(bytes(message.data[: message.len]))
