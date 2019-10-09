"""Parser class for the protocol spoken by FlockCtrl-based drones."""

from flockwave.server.utils import itersubclasses
from hexdump import hexdump

from .errors import ParseError
from .packets import FlockCtrlPacket

__all__ = ("FlockCtrlParser",)


class FlockCtrlParser(object):
    """Parser class for the protocol spoken by FlockCtrl-based drones.

    Attributes:
        packet_log (Optional[logging.Logger]): log to dump the received
            packets into. Useful for debugging purposes.
    """

    def __init__(self):
        """Constructor."""
        self._packet_type_to_packet = self._create_packet_type_mapping()
        self.packet_log = None

    def _create_packet_type_mapping(self):
        """Creates a mapping from the packet type constants of the FlockCtrl
        protocol to all the non-abstract subclasses of FlockCtrlPacket_.

        Returns:
            Dict[int,FlockCtrlPacket]: mapping from packet type constants
                to the corresponding FlockCtrl packet classes
        """
        result = {}
        for cls in itersubclasses(FlockCtrlPacket):
            if hasattr(cls, "PACKET_TYPE"):
                result[cls.PACKET_TYPE] = cls
        return result

    def parse(self, data):
        """Parses the given raw stream of bytes as a FlockCtrl packet.

        Parameters:
            data (bytes): the packet to parse

        Returns:
            FlockCtrlPacket: the parsed packet

        Raises:
            ParseError: if the given raw stream of bytes cannot be parsed
                as a valid FlockCtrl packet.
        """
        if not data:
            raise ParseError("FlockCtrl packet must not be empty")

        if self.packet_log is not None:
            self.packet_log.debug(hexdump(data))

        packet_type = data[0]
        packet_cls = self._packet_type_to_packet.get(packet_type)
        if not packet_cls:
            raise ParseError("unknown packet type: {0}".format(packet_type))

        packet = packet_cls()
        try:
            packet.decode(data)
        except NotImplementedError:
            raise ParseError(
                "decoding of FlockCtrl packet type {0} "
                "is not implemented yet".format(packet_type)
            )
        return packet
