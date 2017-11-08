"""Functions related to the handling of algorithm codes commonly occurring
in FlockCtrl packets.
"""

from struct import Struct

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.registry import RegistryBase

from .utils import unpack_struct


__all__ = ("find_algorithm_name_by_id", "registry")


class AlgorithmRegistry(RegistryBase):
    """Algorithm registry that can return FlockCtrl algorithm objects from
    their numeric IDs commonly used in FlockCtrl packets.
    """

    def register(self, cls):
        """Registers the given Algorithm_ subclass in the registry.

        Parameters:
            cls: the Algorithm_ subclass to registry
        """
        self._entries[cls.ID] = cls()


registry = AlgorithmRegistry()


class Algorithm(object):
    """Interface specification for algorithms that can be implemented on a
    FlockCtrl drone.
    """

    def handle_data_packet(self, packet, uav, mutate):
        """Handles a data packet originating from the given algorithm sent
        by the given UAV.

        Parameters:
            packet (FlockCtrlAlgorithmDataPacket): the data packet that was
                sent by the algorithm running on the given UAV
            uav (FlockCtrlUAV): the UAV that sent the data packet
            mutate (callable): callable that returns a device tree mutator
                when invoked as a context manager
        """
        pass

    @property
    def id(self):
        """Returns the numeric identifier of the algorithm."""
        return self.__class__.ID

    @property
    def name(self):
        """Returns the name of the algorithm."""
        return self.__class__.NAME

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


@registry.register
class DummyAlgorithm(Algorithm):
    ID = 0
    NAME = "dummy"


@registry.register
class AltitudeHoldAlgorithm(Algorithm):
    ID = 1
    NAME = "altitude"


@registry.register
class ChasingAlgorithm(Algorithm):
    ID = 3
    NAME = "chasing"


@registry.register
class EmergencyAlgorithm(Algorithm):
    ID = 5
    NAME = "emergency"


@registry.register
class FlockingAlgorithm(Algorithm):
    ID = 6
    NAME = "flocking"


@registry.register
class GeigerCounterAlgorithm(Algorithm):
    ID = 7
    NAME = "geiger"

    _struct = Struct("<LllhhLLf")

    def handle_data_packet(self, packet, uav, mutate):
        """Inherited."""
        raw_counts = [0, 0]
        (iTOW, lat, lon, amsl, agl, raw_counts[0], raw_counts[1], dose_rate), _ = \
            self._unpack(packet.body)

        # Standardize units coming from the packet
        lat = lat / 1e7          # [1e-7 deg] --> [deg]
        lon = lon / 1e7          # [1e-7 deg] --> [deg]
        amsl = amsl / 1e1        # [dm]       --> [m]
        agl = agl / 1e1          # [dm]       --> [m]

        # Construct the position object
        position = GPSCoordinate(lat=lat, lon=lon, amsl=amsl, agl=agl)

        # Update the UAV devices
        with mutate() as mutator:
            uav.update_geiger_counter(position, iTOW, dose_rate, raw_counts,
                                      mutator)


@registry.register
class ReturnToHomeAlgorithm(Algorithm):
    ID = 8
    NAME = "return_to_home"


@registry.register
class ILandingAlgorithm(Algorithm):
    ID = 9
    NAME = "ilanding"


@registry.register
class LandingAlgorithm(Algorithm):
    ID = 12
    NAME = "landing"


@registry.register
class NinaAlgorithm(Algorithm):
    ID = 14
    NAME = "nina"


@registry.register
class WaypointCloudAlgorithm(Algorithm):
    ID = 17
    NAME = "waypointcloud"


@registry.register
class SnakeAlgorithm(Algorithm):
    ID = 19
    NAME = "snake"


@registry.register
class TrafficAlgorithm(Algorithm):
    ID = 20
    NAME = "traffic"


@registry.register
class VicsekAlgorithm(Algorithm):
    ID = 22
    NAME = "vicsek"


@registry.register
class WaypointAlgorithm(Algorithm):
    ID = 23
    NAME = "waypoint"


def find_algorithm_name_by_id(algorithm_index, handle_unknown=False):
    """Converts the index of an algorithm in a FlockCtrl packet to the
    human-readable name of the corresponding algorithm.

    Parameters:
        registry (AlgorithmRegistry): the algorithm registry to use
        algorithm_index (int): the index of the algorithm
        handle_unknown (bool): whether this function should handle unknown
            algorithm codes by returning an appropriately constructed fake
            algorithm name

    Returns:
        str: the human-readable name of the algorithm

    Raises:
        KeyError: if the index does not belong to a known algorithm and
            ``handle_unknown`` was set to ``False``
    """
    global registry
    try:
        return registry[algorithm_index].name
    except KeyError:
        if handle_unknown:
            return u"unknown ({0})".format(algorithm_index)
        else:
            raise
