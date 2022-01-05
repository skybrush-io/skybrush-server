from dataclasses import dataclass
from enum import IntEnum
from struct import Struct
from typing import ClassVar

__all__ = ("ArtDMXPayload", "ArtNetPacket", "ArtNetOpCode")


class ArtNetOpCode(IntEnum):
    """Supported opcodes in an ArtNet packet."""

    ARTDMX = 0x5000


@dataclass
class ArtNetPacket:
    """Representation of an ArtNet packet."""

    opcode: int
    """Opcode of the ArtNet packet."""

    version: int
    """Version number of the ArtNet protocol."""

    payload: bytes
    """Payload of the packet."""

    _struct: ClassVar[Struct] = Struct(">HH")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Creates an ArtNet packet from its binary representation.

        The header of the packet is not checked; it is assumed that the caller
        already ensured that the packet is an ArtNet packet.
        """
        return cls(
            int.from_bytes(data[8:10], byteorder="little"),
            int.from_bytes(data[10:12], byteorder="big"),
            data[12:],
        )


@dataclass
class ArtDMXPayload:
    """Representation of the payload of an ArtNet packet with the ArtDMX
    opcode.
    """

    sequence: int = 0
    """Sequence number of the ArtNet packet."""

    physical: int = 0
    """Physical port that generated the ArtNet packet."""

    universe: int = 0
    """Universe number that the ArtNet packet is intended to."""

    length: int = 0
    """The total length of the data array."""

    data: bytes = b""
    """The data array."""

    @classmethod
    def from_bytes(cls, data: bytes):
        """Creates an ArtDMX payload from its binary representation."""
        if len(data) < 6:
            return cls()

        length = int.from_bytes(data[4:6], byteorder="big")
        tail = data[6:].ljust(length, b"\x00")

        return cls(data[0], data[1], (data[3] & 0x7F) << 8 + data[2], length, tail)
