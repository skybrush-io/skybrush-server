from dataclasses import dataclass
from struct import Struct

from flockwave.server.model.gps import GPSFixType as OurGPSFixType

from .enums import GPSFixType
from .types import MAVLinkMessage

__all__ = ("DroneShowStatus",)


@dataclass
class DroneShowStatus:
    """Data class representing a Skybrush-specific drone show status object."""

    light: int = 0
    gps_fix: GPSFixType = OurGPSFixType.NO_GPS
    num_satellites: int = 0

    #: Identifier of Skybrush-specific DATA16 show status packets
    TYPE = 0x5B

    #: Structure of Skybrush-specific DATA16 show status packets
    _struct = Struct("<iHBBB")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw body of a MAVLink
        DATA16 packet that has already been truncated to the desired length of
        the packet.
        """
        start_time, light, flags, stage, gps_health = cls._struct.unpack(data[:9])
        return cls(
            light=light,
            gps_fix=GPSFixType.to_ours(gps_health & 0x07),
            num_satellites=gps_health >> 3,
        )

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
