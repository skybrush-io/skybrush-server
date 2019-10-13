"""Classes and functions related to the handling of a wireless Ethernet
link in the extension.
"""

from collections import namedtuple
from functools import partial
from trio import open_memory_channel, open_nursery

from .comm import CommunicationManagerBase

__all__ = ("WirelessCommunicationManager",)


#: Lightweight named tuple to store a packet sending request
PacketSendingRequest = namedtuple("PacketSendingRequest", "packet destination")


class ConnectionTaskManager(object):
    """Lightweight object that receives a connection and then creates an inbound
    and an outbound task for managing the traffic on the connection. The two
    tasks are executed in parallel.
    """

    def __init__(self, inbound_task=None, outbound_task=None):
        """Constructor.

        Parameters:
            inbound_task: a callable that can be invoked with a Connection_
                object and that will handle inbound traffic on the connection.
                When omitted, there will be no inbound task for the
                connection.
            outbound_task: a callable that can be invoked with a Connection_
                object and a ReceiveChannel_ and that will handle outbound
                traffic on the connection. Messages to send will arrive on the
                ReceiveChannel_ and must be passed on to the Connection_. When
                omitted, there will be no outbound task for the connection.
        """
        self._inbound_task = inbound_task
        self._outbound_task = outbound_task

        self._put = None

    async def put(self, request):
        """Puts a packet sending request into the queue of the outbound
        thread.

        Messages that try to be sent while the communication manager is not
        running will be dropped silently.
        """
        if self._put:
            await self._put(request)

    async def run(self, connection):
        """Creates the inbound and outbound tasks in a nursery and runs them
        in parallel.
        """
        tx_queue, rx_queue = open_memory_channel()
        with open_nursery() as nursery, tx_queue:
            try:
                self._put = tx_queue.send
                nursery.start_soon(self._inbound_task, connection)
                nursery.start_soon(self._outbound_task, connection, rx_queue)
            finally:
                self._put = None


class WirelessCommunicationManager(CommunicationManagerBase):
    """Object that manages the communication with an UAV using a wireless
    UDP link.

    The manager creates a single green thread for receiving inbound UDP
    packets. Outbound UDP packets are sent on the main thread because they
    are assumed not to block the thread, and the UDP protocol does not
    provide us with acknowledgments anyway.
    """

    def __init__(self, log, port=4243):
        """Constructor.

        Parameters:
            log: the logger where the communication manager should log its
                messages
            port (int): the port to use when sending a packet to a UAV
        """
        super().__init__("wireless", log=log)

        self.port = port

        self._broadcast_tasks = ConnectionTaskManager(
            inbound_task=partial(
                WirelessInboundThread,
                manager=self,
                callback=self._handle_inbound_packet,
            ),
            outbound_task=partial(WirelessOutboundThread, manager=self),
        )
        self._unicast_tasks = ConnectionTaskManager(
            inbound_task=partial(
                WirelessInboundThread,
                manager=self,
                callback=self._handle_inbound_packet,
            ),
            outbound_task=partial(WirelessOutboundThread, manager=self),
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
        unicast packets.
        """
        return self._unicast_threads.connection

    @unicast_connection.setter
    def unicast_connection(self, value):
        self._unicast_threads.connection = value

    async def send_packet(self, packet, destination=None):
        """Requests the communication manager to send the given FlockCtrl
        packet to the given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (str): the IP address to send the packet to
        """
        if destination is None:
            put = self._broadcast_tasks.put
        else:
            put = self._unicast_tasks.put

        req = PacketSendingRequest(packet=packet, destination=(destination, self.port))
        await put(req)

    def _handle_inbound_packet(self, address, packet):
        """Handler function called for every inbound UDP packet read by
        the inbound connection handler tasks.

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
