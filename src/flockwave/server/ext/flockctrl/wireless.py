"""Classes and functions related to the handling of a wireless Ethernet
link in the extension.
"""

from collections import namedtuple
from functools import partial

from .comm import CommunicationManagerBase

__all__ = ("WirelessCommunicationManager",)


#: Lightweight named tuple to store a packet sending request
PacketSendingRequest = namedtuple("PacketSendingRequest", "packet destination")


class ConnectionThreadManager(object):
    """Lightweight object that receives a connection in one of its
    properties and then creates an inbound and an outbound thread for
    managing the traffic on the connection.
    """

    def __init__(self, inbound_thread_factory=None, outbound_thread_factory=None):
        """Constructor.

        Parameters:
            inbound_thread_factory (Optional[callable]): a callable that can
                be invoked with a ConnectionBase_ object as its only argument
                and that creates an appropriate thread for handling the
                inbound traffic on that connection. When omitted, there will
                be no inbound thread for the connection.
            outbound_thread_factory (Optional[callable]): a callable that can
                be invoked with a ConnectionBase_ object as its only argument
                and that creates an appropriate thread for handling the
                outbound traffic on that connection. The thread is expected
                to provide a ``put`` method with which one can feed packet
                sending requests into the thread. When this argument is
                omitted, there will be no outbound thread for the connection.
        """
        self._inbound_thread_factory = inbound_thread_factory
        self._outbound_thread_factory = outbound_thread_factory

        self._connection = None

        self._inbound_thread = None
        self._outbound_thread = None

        self._put = None

    @property
    def connection(self):
        """The connection associated to the thread manager."""
        return self._connection

    @connection.setter
    def connection(self, value):
        if self._connection == value:
            return

        if self._connection is not None:
            if self._inbound_thread is not None:
                self._inbound_thread.kill()
                self._inbound_thread = None

            if self._outbound_thread is not None:
                self._outbound_thread.kill()
                self._outbound_thread = None
                self._put = None

        self._connection = value

        if self._connection is not None:
            if self._inbound_thread_factory is not None:
                thread = self._inbound_thread_factory(self._connection)
                self._inbound_thread = spawn(thread.run)

            if self._outbound_thread_factory is not None:
                thread = self._outbound_thread_factory(self._connection)
                self._put = thread.put
                self._outbound_thread = spawn(thread.run)

    def put(self, request):
        """Puts a packet sending request into the queue of the outbound
        thread.
        """
        return self._put(request)


class WirelessCommunicationManager(CommunicationManagerBase):
    """Object that manages the communication with an UAV using a wireless
    UDP link.

    The manager creates a single green thread for receiving inbound UDP
    packets. Outbound UDP packets are sent on the main thread because they
    are assumed not to block the thread, and the UDP protocol does not
    provide us with acknowledgments anyway.
    """

    def __init__(self, ext, port=4243):
        """Constructor.

        Parameters:
            ext (FlockCtrlDronesExtension): the extension that owns this
                manager
            port (int): the port to use when sending a packet to a UAV
        """
        super(WirelessCommunicationManager, self).__init__(ext, "wireless")

        self.port = port

        self._broadcast_threads = ConnectionThreadManager(
            inbound_thread_factory=partial(
                WirelessInboundThread,
                manager=self,
                callback=self._handle_inbound_packet,
            ),
            outbound_thread_factory=partial(WirelessOutboundThread, manager=self),
        )
        self._unicast_threads = ConnectionThreadManager(
            inbound_thread_factory=partial(
                WirelessInboundThread,
                manager=self,
                callback=self._handle_inbound_packet,
            ),
            outbound_thread_factory=partial(WirelessOutboundThread, manager=self),
        )

    @property
    def broadcast_connection(self):
        """The UDP connection object that is used to send and receive
        broadcast packets.
        """
        return self._broadcast_threads.connection

    @broadcast_connection.setter
    def broadcast_connection(self, value):
        self._broadcast_threads.connection = value

    @property
    def unicast_connection(self):
        """The UDP connection object that is used to send and receive
        broadcast packets.
        """
        return self._unicast_threads.connection

    @unicast_connection.setter
    def unicast_connection(self, value):
        self._unicast_threads.connection = value

    def send_packet(self, packet, destination=None):
        """Requests the communication manager to send the given FlockCtrl
        packet to the given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (str): the IP address to send the packet to
        """
        if destination is None:
            put = self._broadcast_threads.put
        else:
            put = self._unicast_threads.put

        req = PacketSendingRequest(packet=packet, destination=(destination, self.port))
        put(req)

    def _handle_inbound_packet(self, address, packet):
        """Handler function called for every inbound UDP packet read by
        the inbound green thread.

        Parameters:
            address (tuple): the source IP address and port that the packet
                was received from
            packet (bytes): the raw bytes that were received
        """
        address, _ = address  # separate the port, we don't need it
        self._parse_and_emit_packet(packet, address)


class WirelessInboundThread(object):
    """Green thread that reads incoming packets from a wireless link
    connection and calls a handler function on the communication manager
    for every one of them.

    The thread is running within the application context of the Flockwave
    server application.
    """

    def __init__(self, connection, manager, callback):
        """Constructor.

        Parameters:
            connection (UDPSocketConnection): the UDP connection object that
                should be used to wait for inbound packets
            manager (WirelessCommunicationManager): the communication manager
                that owns this thread
            callback (callable): function to call when a packet is read from
                the connection. It will be called with the address and the
                packet as two keyword arguments: ``address`` and ``packet``.
        """
        self.manager = manager
        self._callback = callback
        self._connection = connection

    def _error_callback(self, exception):
        """Callback function called when an exception happens while waiting
        for a data frame.
        """
        self.manager.log.exception(exception)

    def run(self):
        """Waits for incoming frames on the associated low-level wireless
        connection and dispatches a signal for every one of them.
        """
        while True:
            # TODO(ntamas): this is now async!
            self._connection.wait_until_connected()
            self._read_next_packet()

    def _read_next_packet(self):
        """Reads the next packet from the associated UDP socket connection
        and call the appropriate function on the manager with the packet
        received and the address that it was received from.
        """
        try:
            data, address = self._connection.read(blocking=True)
            self._callback(address=address, packet=data)
        except Exception as ex:
            self._error_callback(ex)


class WirelessOutboundThread(object):
    """Green thread that sends outbound packets to a wireless link
    connection. The outbound packets must be placed into a queue that
    is owned by this thread.

    The thread is running within the application context of the Flockwave
    server application.
    """

    def __init__(self, connection, manager):
        """Constructor.

        Parameters:
            connection (ConnectionBase): the wireless connection object
            manager (WirelessCommunicationManager): the communication manager
                that owns this thread
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

    def put(self, request):
        """Puts a packet sending request into the queue of this thread.

        Parameters:
            request (PacketSendingRequest): the request object containing
                the packet to send and its destination address in an
                (IP address, port) tuple
        """
        self._queue.put(request)

    def run(self):
        """Waits for outbound frames to send on the queue owned by this
        thread, and sends each of them via the wireless connection.
        """
        while True:
            try:
                self._serve_request(self._queue.get())
            except Exception as ex:
                self._error_callback(ex)

    def _serve_request(self, request):
        """Serves a wireless packet sending request by sending a packet to a
        given destination.

        Parameters:
            request (PacketSendingRequest): the request object containing
                the packet to send and its destination address in an
                (IP address, port) tuple
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
