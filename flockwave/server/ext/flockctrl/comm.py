"""Base classes for implementing communication managers that facilitate
communication between UAVs and a ground station via some communication
link (e.g., XBee or wireless).
"""

from blinker import Signal

from .errors import ParseError
from .parser import FlockCtrlParser


class CommunicationManagerBase(object):
    """Abstract communication manager base class and interface specification.

    Attributes:
        identifier (str): unique identifier of the communication manager;
            see the constructor documentation for its purpose
        on_packet (Signal): signal that is emitted when the communication
            manager receives a new data packet from an UAV. The signal is
            called with the parsed data packet as its only argument.
    """

    on_packet = Signal()

    def __init__(self, ext, identifier):
        """Constructor.

        Parameters:
            ext (FlockCtrlDronesExtension): the extension that owns this
                manager
            identifier (str): unique identifier of this communication
                mananger; e.g., ``xbee`` for XBee packets or ``wireless``
                for a wireless network. The purpose of this identifier is
                that the ``(identifier, address)`` pair of a UAV must be
                unique (in other words, each UAV must have a unique address
                *within* each communication network that we handle)
        """
        self.ext = ext
        self.identifier = identifier
        self._parser = FlockCtrlParser()

    @property
    def log(self):
        """Returns the logger of the extension that owns this manager.

        Returns:
            Optional[logging.Logger]: the logger of the extension that owns
                this manager, or ``None`` if the manager is not associated
                to an extension yet.
        """
        return self.ext.log if self.ext else None

    def _parse_and_emit_packet(self, data, address):
        """Parses a raw data packet received from the given address using
        the FlockCtrl protocol parser and emits it with the ``on_packet``
        signal so listeners can act on it.

        Parameters:
            data (bytes): the raw data received over the communication
                channel, stripped from all medium-specific framing. This
                will be fed directly into a FlockCtrlParser_ object
            address (object): the address where the data was received from

        Returns:
            bool: whether the packet was processed successfully and the
                ``on_packet`` signal was emitted
        """
        try:
            packet = self._parser.parse(data)
        except ParseError as ex:
            self.log.warn(
                "Failed to parse FlockCtrl packet of length "
                "{0}: {1!r}".format(len(data), data[:32])
            )
            self.log.exception(ex)
            return False

        packet.source = (self.identifier, address)
        self.on_packet.send(self, packet=packet)
        return True

    def send_packet(self, packet, destination=None):
        """Requests the communication manager to send the given FlockCtrl
        packet to the given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (Optional[object]): the destination address to
                send the packet to. Its format depends on the concrete
                communication channel; for instance, it will be a ``bytes``
                object for the XBee channel. ``None`` means to send a
                broadcast packet that is targeted to all UAVs accessible
                via the communication channel.

        Raises:
            NotImplementedError: if the operation is not implemented for the
                concrete channel
        """
        raise NotImplementedError
