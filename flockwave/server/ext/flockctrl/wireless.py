"""Classes and functions related to the handling of a wireless Ethernet
link in the extension.
"""

from collections import namedtuple
from eventlet.event import Event
from eventlet import Queue, spawn

from .comm import CommunicationManagerBase

__all__ = ("WirelessCommunicationManager", )


#: Lightweight named tuple to store a packet sending request
PacketSendingRequest = namedtuple(
    "PacketSendingRequest",
    "packet destination"
)


class WirelessCommunicationManager(CommunicationManagerBase):
    """Object that manages the communication with an UAV using a wireless
    UDP link.

    The manager creates a single green thread for receiving inbound UDP
    packets. Outbound UDP packets are sent on the main thread because they
    are assumed not to block the thread, and the UDP protocol does not
    provide us with acknowledgments anyway.
    """

    def __init__(self, ext):
        """Constructor.

        Parameters:
            ext (FlockCtrlDronesExtension): the extension that owns this
                manager
        """
        super(WirelessCommunicationManager, self).__init__(ext, "wireless")
        self._inbound_thread = None
        self._outbound_thread = None
        self._packet_queue = None
        self._connection = None

    @property
    def connection(self):
        """The UDP connection object that is used to send and receive
        packets.
        """
        return self._connection

    @connection.setter
    def connection(self, value):
        if self._connection == value:
            return

        if self._connection is not None:
            self._inbound_thread.kill()
            self._outbound_thread.kill()
            self._packet_queue = None

        self._connection = value

        if self._connection is not None:
            thread = WirelessInboundThread(self, self._connection)
            self._inbound_thread = spawn(thread.run)

            thread = WirelessOutboundThread(self, self._connection)
            self._packet_queue = thread.queue
            self._outbound_thread = spawn(thread.run)

    def send_packet(self, packet, destination=None):
        """Requests the communication manager to send the given FlockCtrl
        packet to the given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination ((str, int)): the IP address and port to send the
                packet to
        """
        if destination is None:
            raise ValueError("broadcasting is not supported")

        req = PacketSendingRequest(packet=packet, destination=destination)
        self._packet_queue.put(req)

    def _handle_inbound_packet(self, address, packet):
        """Handler function called for every inbound UDP packet read by
        the inbound green thread.

        Parameters:
            address (tuple): the source IP address and port that the packet
                was received from
            packet (bytes): the raw bytes that were received
        """
        self._parse_and_emit_packet(packet, address)


class WirelessInboundThread(object):
    """Green thread that reads incoming packets from a wireless link
    connection and calls a handler function on the communication manager
    for every one of them.

    The thread is running within the application context of the Flockwave
    server application.
    """

    def __init__(self, manager, connection):
        """Constructor.

        Parameters:
            manager (WirelessCommunicationManager): the communication manager
                that owns this thread
            connection (UDPSocketConnection): the UDP connection object that
                should be used to wait for inbound packets
        """
        self.manager = manager
        self._connection = connection
        self._connection_is_open_event = None

    def _callback(self, address, packet):
        """Callback function called for every single packet read from the
        UDP socket.

        Parameters:
            address (tuple): the source IP address and port that the packet
                was received from
            packet (bytes): the raw bytes that were received
        """
        self.manager._handle_inbound_packet(address=address, packet=packet)

    def _error_callback(self, exception):
        """Callback function called when an exception happens while waiting
        for a data frame.
        """
        self.manager.log.exception(exception)

    def run(self):
        """Waits for incoming frames on the associated low-level wireless
        connection and dispatches a signal for every one of them.
        """
        with self.manager.app_context():
            while True:
                self._wait_until_connection_is_open()
                self._read_next_packet()

    def _on_connection_connected(self, sender):
        """Signal handler that is called when the connection object
        associated to this green thread becomes open.
        """
        if self._connection_is_open_event:
            self._connection_is_open_event.send(True)
            self._connection_is_open_event = None

    def _read_next_packet(self):
        """Reads the next packet from the associated UDP socket connection
        and call the appropriate function on the manager with the packet
        received and the address that it was received from.
        """
        try:
            data, address = self._connection.read(blocking=True)
            self._callback(packet=data, address=address)
        except Exception as ex:
            self._error_callback(ex)

    def _wait_until_connection_is_open(self):
        """Checks whether the connection associated to the thread is open.
        If it is, it returns immediately. Otherwise, it creates an event
        object and blocks on it until the connection becomes open.
        """
        if self._connection.is_connected:
            return

        signal = self._connection.connected
        self._connection_is_open_event = Event()
        with signal.connected_to(self._on_connection_connected,
                                 sender=self._connection):
            self._connection_is_open_event.wait()


class WirelessOutboundThread(object):
    """Green thread that sends outbound packets to a wireless link
    connection. The outbound packets must be placed into a queue that
    is owned by this thread.

    The thread is running within the application context of the Flockwave
    server application.
    """

    def __init__(self, manager, connection):
        """Constructor.

        Parameters:
            manager (XBeeCommunicationManager): the communication manager
                that owns this thread
            connection (ConnectionBase): the wireless connection object
        """
        self.connection = connection
        self.manager = manager
        self._queue = Queue()

    def _error_callback(self, exception):
        """Callback function called when an exception happens while sending
        a data frame.
        """
        self.manager.log.exception(exception)

    @property
    def queue(self):
        """The queue that should be used to send outbound packets to this
        thread.
        """
        return self._queue

    def run(self):
        """Waits for outbound frames to send on the queue owned by this
        thread, and sends each of them via the wireless connection.
        """
        with self.manager.app_context():
            while True:
                try:
                    self._serve_request(self._queue.get())
                except Exception as ex:
                    self._error_callback(ex)

    def _serve_request(self, request):
        """Serves a wireless packet sending request by sending a packet to a
        given destination.

        Parameters:
            request (PacketSendingRequest): the request object
                containing the packet to send and its destination address
                in an (IP address, port) tuple
        """
        data = request.packet.encode()
        while data:
            num_sent = self.connection.write(data, request.destination)
            if num_sent < 0:
                # There was an error while sending the packet so let's
                # skip it entirely
                data = b""
            else:
                data = data[num_sent:]
