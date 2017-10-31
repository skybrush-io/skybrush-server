"""Classes representing the different types of FlockCtrl packets."""

from __future__ import division

import zlib

from abc import ABCMeta, abstractproperty
from blinker import Signal
from builtins import range
from collections import defaultdict
from datetime import datetime
from flockwave.gps.vectors import GPSCoordinate, VelocityNED
from flockwave.server.utils import datetime_to_unix_timestamp
from future.utils import with_metaclass
from six import byte2int, int2byte
from struct import Struct
from time import time

from .algorithms import find_algorithm_name_by_id, \
    registry as algorithm_registry
from .utils import unpack_struct

__all__ = ("FlockCtrlPacket", "ChunkedPacketAssembler")


class FlockCtrlPacket(with_metaclass(ABCMeta, object)):
    """Common interface specification for all FlockCtrl-related packets."""

    @abstractproperty
    def source(self):
        """The source medium and address of the packet, if known. ``None``
        if the packet was created locally. Otherwise it is a tuple containing
        the source medium (e.g., ``xbee``) and the address whose format is
        specific to the source medium.
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
        self._source = None

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        self._source = value

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
        return unpack_struct(spec or self._struct, data)


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
    _struct = Struct("<xBBBLllhhhhhhBLB8sL")

    def decode(self, data):
        (self.id, self.algo_and_vehicle, self.choreography_index,
            self.iTOW, lon, lat, amsl, agl,
            velN, velE, velD, heading, voltage,
            self.flags, self.error, self.debug, self.clock_status), _ = \
            self._unpack(data)

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
    def algorithm(self):
        """The algorithm that is currently being executed by the drone.

        Returns:
            Algorithm: the algorithm that is currently being executed by
                the drone.

        Raises:
            KeyError: if the algorithm that the packet refers to is not
                known to the server
        """
        return algorithm_registry[self.algorithm_id]

    @property
    def algorithm_id(self):
        """The numeric ID of the algorithm that is currently being executed
        by the drone.

        Returns:
            int: the numeric index of the algorithm
        """
        return self.algo_and_vehicle & 0x1f

    @property
    def algorithm_name(self):
        """The human-readable description of the algorithm that is
        currently being executed by the drone.

        Returns:
            str: the name of the algorithm
        """
        return find_algorithm_name_by_id(self.algorithm_id)

    @property
    def location(self):
        """The location of the drone according to the status packet.

        Returns:
           GPSCoordinate: the location of the drone
        """
        return GPSCoordinate(lat=self.lat, lon=self.lon,
                             amsl=self.amsl, agl=self.agl)

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


class FlockCtrlRTCMDataPacket(FlockCtrlPacketBase):
    """Packet containing an RTCM payload for DGPS forwarding."""

    PACKET_TYPE = 8

    def decode(self, data):
        # Not interested yet
        pass


class FlockCtrlClockSynchronizationPacket(FlockCtrlPacketBase):
    """Packet containing a request for the drone to synchronize one of its
    internal clocks with the ground station.
    """

    PACKET_TYPE = 9
    _struct = Struct("<xBBQdH")

    def __init__(self, sequence_id, clock_id, running, local_timestamp,
                 ticks, ticks_per_second):
        """Constructor.

        Parameters:
            sequence_id (int): the sequence ID of the packet. This can be
                used by UAVs later on in acknowledgment messages.
            clock_id (int): the index of the clock on the UAV that should
                be synchronized
            running (bool): whether the clock should be running or not
            local_timestamp (float or datetime): the local time on the
                server, expressed as the number of seconds since the Unix
                epoch in UTC, or an appropriate datetime object (that will
                then be converted). When you use a datetime object here,
                make sure that it is timezone-aware (to avoid confusion)
            ticks (float): the number of clock ticks at the local time.
            ticks_per_second (int): the number of clock ticks per second.

        Raises:
            ValueError: if the given timestamp is not timezone-aware
        """
        self.sequence_id = int(sequence_id)
        self.clock_id = int(clock_id)
        self.running = bool(running)

        if isinstance(local_timestamp, datetime):
            local_timestamp = datetime_to_unix_timestamp(local_timestamp)

        self.local_timestamp = float(local_timestamp)
        self.ticks = float(ticks)
        self.ticks_per_second = int(ticks_per_second)

    def encode(self):
        return int2byte(self.PACKET_TYPE) + self._struct.pack(
            self.sequence_id,
            (self.clock_id & 0x7F) + (128 if self.running else 0),
            self.local_timestamp, self.ticks, self.ticks_per_second
        )[1:]


class FlockCtrlFileUploadKeepalivePacket(FlockCtrlPacketBase):
    """Packet containing an XBee file upload 'keepalive' packet."""

    PACKET_TYPE = 10

    def decode(self, data):
        # Not interested yet
        pass


class FlockCtrlVersionPacket(FlockCtrlPacketBase):
    """Packet containing a version number request/response."""

    PACKET_TYPE = 11

    def decode(self, data):
        # Not interested yet
        pass


class FlockCtrlIdMappingPacket(FlockCtrlPacketBase):
    """Packet containing a mapping from numeric 'mission IDs' to the
    absolute numeric UAV IDs.
    """

    PACKET_TYPE = 12

    def decode(self, data):
        # Not interested yet
        pass


class FlockCtrlPrearmStatusPacket(FlockCtrlPacketBase):
    """Packet containing detailed information about the status of the prearm
    checking when the UAV is in the prearm state.
    """

    PACKET_TYPE = 13

    def decode(self, data):
        # Not interested yet
        pass


class FlockCtrlAlgorithmDataPacket(FlockCtrlPacketBase):
    """Packet containing algorithm-specific data that can be parsed by the
    corresponding algorithm.
    """

    PACKET_TYPE = 14
    _struct = Struct("<xBB")

    def __init__(self):
        super(FlockCtrlAlgorithmDataPacket, self).__init__()
        self.algorithm_id = None
        self.uav_id = None
        self.body = None

    @property
    def algorithm(self):
        """The algorithm that sent the data packet.

        Returns:
            Algorithm: the algorithm that sent the data packet

        Raises:
            KeyError: if the algorithm that the packet refers to is not
                known to the server
        """
        return algorithm_registry[self.algorithm_id]

    @property
    def algorithm_name(self):
        """The human-readable description of the algorithm that sent the
        data packet.

        Returns:
            str: the name of the algorithm
        """
        return find_algorithm_name_by_id(self.algorithm_id)

    def decode(self, data):
        (self.algorithm_id, self.uav_id), self.body = self._unpack(data)
        self.algorithm_id = self.algorithm_id & 0x1f


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
        if packet.source is None:
            raise ValueError("inbound chunked packet must have a "
                             "source address")

        now = time()

        messages = self._messages[packet.source]
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
                                       source=packet.source)
            del messages[packet.sequence_id]
            if not messages:
                del self._messages[packet.source]

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
            for i in range(n):
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
        self._messages[packet.source][packet.sequence_id] = msg_data
        return msg_data
