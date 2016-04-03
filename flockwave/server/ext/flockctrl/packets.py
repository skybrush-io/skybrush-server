"""Classes representing the different types of FlockCtrl packets."""

from __future__ import division

from abc import ABCMeta
from flockwave.gps.vectors import Altitude, GPSCoordinate, VelocityNED
from six import with_metaclass
from struct import Struct
from struct import error as StructError

from .errors import ParseError


__all__ = ("FlockCtrlPacket", )


class FlockCtrlPacket(with_metaclass(ABCMeta, object)):
    """Common interface specification for all FlockCtrl-related packets."""

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
        pass

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
            tuple: the unpacked values

        Raises:
            ParseError: if the given byte array cannot be unpacked
        """
        spec = spec or self._struct
        try:
            return spec.unpack(data)
        except StructError as ex:
            raise ParseError(ex.message)


class FlockCtrlStatusPacket(FlockCtrlPacketBase):
    """Status packet sent by FlockCtrl-based drones at regular intervals."""

    PACKET_TYPE = 0
    _struct = Struct("<xBBBLllhhlllhBLB8s")

    def __init__(self):
        super(FlockCtrlStatusPacket, self).__init__()

    def decode(self, data):
        self.id, self.algo_and_vehicle, self.choreography_index, \
            self.iTOW, lon, lat, amsl, agl, \
            velN, velE, velD, heading, voltage, \
            self.flags, self.error, self.debug = self._unpack(data)

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


class FlockCtrlChunkedPacket(FlockCtrlPacket):
    """Abstract base class for chunked FlockCtrl packets that break down
    some piece of data in transit to several packets.
    """

    pass


class FlockCtrlCommandResponsePacketBase(FlockCtrlChunkedPacket):
    """Base class for packets that contain a response to a command that was
    sent from the ground station to the drone.
    """

    pass


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
