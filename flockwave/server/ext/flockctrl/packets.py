"""Classes representing the different types of FlockCtrl packets."""

from __future__ import division

import zlib

from abc import ABCMeta, abstractproperty
from blinker import Signal
from collections import defaultdict
from flockwave.gps.vectors import Altitude, GPSCoordinate, VelocityNED
from six import add_metaclass, byte2int, int2byte
from struct import Struct
from struct import error as StructError
from time import time

from .errors import ParseError


__all__ = ("FlockCtrlPacket", "ChunkedPacketAssembler")


@add_metaclass(ABCMeta)
class FlockCtrlPacket(object):
    """Common interface specification for all FlockCtrl-related packets."""

    @abstractproperty
    def source_address(self):
        """The source address of the packet, if known. ``None`` if the
        packet was created locally.
        """
        raise NotImplementedError

    def decode(self, data):
        """Initializes the data fields of the packet from the given raw
        bytes.

        It is the responsibility of the caller to ensure that the first
        byte of the data object corresponds to the packet type on which
        this method is called.

        Parameters:
            data (bytes): the raw, byte-level representation of the packet
                when it is transmitted over the wire.

        Raises:
            ParseError: when the data fields cannot be parsed
            NotImplementedError: if the deserialization of this packet is not
                implemented yet
        """
        raise NotImplementedError

    def encode(self):
        """Encodes the packet into a raw bytes object that can represent
        the packet over the wire.

        Returns:
            bytes: the encoded representation of the packet

        Raises:
            NotImplementedError: if the serialization of this packet is not
                implemented yet
        """
        raise NotImplementedError


class FlockCtrlPacketBase(FlockCtrlPacket):
    """Abstract base class for all FlockCtrl-related packets."""

    def __init__(self):
        self._source_address = None

    @property
    def source_address(self):
        return self._source_address

    @source_address.setter
    def source_address(self, value):
        self._source_address = value

    def _unpack(self, data, spec=None):
        """Unpacks some data from the given raw bytes object according to
        the format specification (given as a Python struct).

        This method is a thin wrapper around ``struct.Struct.unpack()`` that
        turns ``struct.error`` exceptions into ParseError_

        Parameters:
            data (bytes): the bytes to unpack
            spec (Optional[Struct]): the specification of the format of the
                byte array to unpack. When ``None``, the function falls back
                to ``self._struct``

        Returns:
            tuple: the unpacked values as a tuple and the remainder of the
                data that was not parsed using the specification

        Raises:
            ParseError: if the given byte array cannot be unpacked
        """
        spec = spec or self._struct
        size = spec.size
        try:
            return spec.unpack(data[:size]), data[size:]
        except StructError as ex:
            raise ParseError(ex.message)


class FlockCtrlChunkedPacket(FlockCtrlPacketBase):
    """Abstract base class for chunked FlockCtrl packets that break down
    some piece of data in transit to several packets.

    Attributes:
        sequence_id (int): the sequence ID of the chunked packet.
        num_chunks (int): the number of chunks to expect in this chunked
            packet sequence.
        chunk_id (int): the index of the current packet within the
            chunked packet sequence.
    """

    def __init__(self):
        super(FlockCtrlChunkedPacket, self).__init__()
        self.sequence_id = None
        self.chunk_id = None
        self.num_chunks = None
        self.body = None

    def decode(self, data):
        self.sequence_id, self.num_chunks, self.chunk_id = \
            [byte2int(x) for x in data[1:4]]
        self.body = data[4:]


class FlockCtrlCommandResponsePacketBase(FlockCtrlChunkedPacket):
    """Base class for packets that contain a response to a command that was
    sent from the ground station to the drone.
    """

    pass


class FlockCtrlStatusPacket(FlockCtrlPacketBase):
    """Status packet sent by FlockCtrl-based drones at regular intervals."""

    PACKET_TYPE = 0
    _struct = Struct("<xBBBLllhhlllhBLB8s")

    def decode(self, data):
        (self.id, self.algo_and_vehicle, self.choreography_index,
            self.iTOW, lon, lat, amsl, agl,
            velN, velE, velD, heading, voltage,
            self.flags, self.error, self.debug), _ = self._unpack(data)

        # Standardize units coming from the packet
        self.lat = lat / 1e7          # [1e-7 deg] --> [deg]
        self.lon = lon / 1e7          # [1e-7 deg] --> [deg]
        self.amsl = amsl / 1e1        # [dm]       --> [m]
        self.agl = agl / 1e1          # [dm]       --> [m]
        self.velN = velN / 1e2        # [cm/s]     --> [m/s]
        self.velE = velE / 1e2        # [cm/s]     --> [m/s]
        self.velD = velD / 1e2        # [cm/s]     --> [m/s]
        self.heading = heading / 1e2  # [1e-2 deg] --> [deg]
        self.voltage = voltage / 1e1  # [0.1V]     --> [V]

    @property
    def location(self):
        """The location of the drone according to the status packet.

        Returns:
           GPSCoordinate: the location of the drone
        """
        return GPSCoordinate(lat=self.lat, lon=self.lon,
                             alt=Altitude.msl(self.amsl))

    @property
    def velocity(self):
        """The velocity of the drone according to the status packet.

        Returns:
            VelocityNED: the velocity of the drone
        """
        return VelocityNED(north=self.velN, east=self.velE, down=self.velD)


class FlockCtrlCommandRequestPacket(FlockCtrlPacketBase):
    """Packet containing a request for the drone to execute a given console
    command.
    """

    PACKET_TYPE = 1

    def __init__(self, command):
        """Constructor.

        Parameters:
            command (bytes): the command to send
        """
        super(FlockCtrlCommandRequestPacket, self).__init__()
        self.command = command

    def encode(self):
        return int2byte(self.PACKET_TYPE) + self.command


class FlockCtrlCommandResponsePacket(FlockCtrlCommandResponsePacketBase):
    """Packet containing an uncompressed response to a command that was
    sent from the ground station to the drone, or a part of it.
    """

    PACKET_TYPE = 2


class FlockCtrlCompressedCommandResponsePacket(
        FlockCtrlCommandResponsePacketBase):
    """Packet containing a compressed response to a command that was
    sent from the ground station to the drone, or a part of it.
    """

    PACKET_TYPE = 3


class ChunkedPacketAssembler(object):
    """Object that assembles a command response from its chunks that
    arrive in consecutive FlockCtrlChunkedPacket_ packets.

    Attributes:
        packet_assembled (Signal): signal sent with the concatenated body of
            the assembled packet and the source address of the device that
            sent the packet
    """

    packet_assembled = Signal()

    def __init__(self):
        """Constructor."""
        self._messages = defaultdict(dict)

    def add_packet(self, packet, compressed=False):
        """Adds the given chunked packet to the chunk assembler for further
        processing.

        Parameters:
            packet (FlockCtrlChunkedPacket): the chunked packet to process.
                It must have a sender address that is not set to ``None``
                because the packet assembler has to group inbound packets
                by the senders.
            compressed (bool): whether the body of the packet is assumed
                to be compressed
        """
        if packet.source_address is None:
            raise ValueError("inbound chunked packet must have a "
                             "source address")

        now = time()

        messages = self._messages[packet.source_address]
        msg_data = messages.get(packet.sequence_id)
        if msg_data is None:
            # This is the first time we see this sequence id
            msg_data = self._notify_new_packet(packet)
        elif msg_data["num_chunks"] != packet.num_chunks:
            # Probably we have a leftover message from a previous attempt
            msg_data = self._notify_new_packet(packet)

        msg_data["chunks"][packet.chunk_id] = packet.body
        msg_data["compressed"] = compressed
        msg_data["time"] = now
        msg_data["last_chunk"] = packet.chunk_id

        if len(msg_data["chunks"]) == msg_data["num_chunks"]:
            body = b"".join(
                body for index, body in sorted(msg_data["chunks"].items())
            )
            if msg_data["compressed"]:
                body = zlib.decompress(body)
            self.packet_assembled.send(self, body=body,
                                       source_address=packet.source_address)
            del messages[packet.sequence_id]
            if not messages:
                del self._messages[packet.source_address]
        print(repr(self._messages))

    def get_chunk_info(self, sequence_id):
        """Returns a string representing which chunks have arrived already
        from the packet with the given sequence ID.

        Parameters:
            sequence_id (int): the sequence ID

        Returns:
            str: a string containing the sequence ID, whether the sequence
                is compressed, and the status of each chunk
                (# = arrived, * = last one that arrived, space = not arrived)
        """
        msg_data = self._messages.get(sequence_id)
        if not msg_data:
            chunk_chars = []
        else:
            n = msg_data["num_chunks"]
            chunk_chars = [" "] * n
            for i in xrange(n):
                if i in msg_data["chunks"]:
                    chunk_chars[i] = "#"
            last_chunk_id = msg_data.get("last_chunk")
            chunk_chars[last_chunk_id] = "*"

        chunk_chars = "".join(chunk_chars)
        return "#%d%s [%s]" % (sequence_id,
                               "C" if msg_data["compressed"] else " ",
                               "".join(chunk_chars))

    def _notify_new_packet(self, packet):
        """Notifies the response chunk assembler that it should anticipate
        a new frame with the given sequence ID.
        """
        msg_data = dict(
            chunks=dict(),
            num_chunks=packet.num_chunks,
            last_chunk=None
        )
        self._messages[packet.source_address][packet.sequence_id] = msg_data
        return msg_data
