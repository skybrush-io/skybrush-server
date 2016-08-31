"""Classes and functions related to the handling of an XBee radio
in the extension.
"""

from __future__ import absolute_import

from blinker import Signal
from collections import namedtuple, Counter
from eventlet import Queue, spawn, spawn_after
from six import byte2int, int2byte
from random import random

from .errors import ParseError
from .parser import FlockCtrlParser


__all__ = ("XBeeCommunicationManager", )


#: Broadcast destination address for the XBee radio
_XBEE_BROADCAST_ADDRESS = b"\x00\x00\x00\x00\x00\x00\xff\xff"


#: Mapping from XBee delivery status codes to human-readable descriptions
_xbee_delivery_status_code_names = {
    0x00: "Success",
    0x01: "MAC ACK failure",
    0x02: "CCA failure",
    0x15: "Invalid destination endpoint",
    0x21: "Network ACK failure",
    0x22: "Not joined to network",
    0x23: "Self-addressed",
    0x24: "Address not found",
    0x25: "Route not found",
    0x26: "Broadcast source failed to hear a neighbor relay the message",
    0x2B: "Invalid binding table index",
    0x2C: "Resource error (lack of free timers, buffers etc)",
    0x2D: "Attempted broadcast with APS transmission",
    0x2E: "Attempted unicast with APS transmission, but EE=0",
    0x32: "Resource error (lack of free timers, buffers etc)",
    0x74: "Data payload too large",
    0x75: "Indirect message unrequested"
}

#: Lightweight named tuple to store a packet sending request
XBeePacketSendingRequest = namedtuple(
    "XBeePacketSendingRequest",
    "packet destination needs_acknowledgment"
)


def xbee_delivery_status_code_to_name(code):
    """Returns a human-readable string describing an XBee delivery status.

    Parameters:
        code (int): the XBee delivery status code

    Returns:
        str: the description of the XBee delivery status
    """
    return _xbee_delivery_status_code_names.get(code, "Unknown status")


class XBeeCommunicationManager(object):
    """Object that manages the communication with an XBee radio device.

    The manager creates two green threads; one for receiving inbound XBee
    packets and one for sending outbound XBee packets. It also takes care
    of watching delivery status information for the outbound packets and
    repeating the packets if the delivery failed.

    Attributes:
        on_packet (Signal): signal that is emitted when the communication
            manager receives a new data packet from the XBee radio. The
            signal is called with the parsed data packet as its only
            argument.
    """

    on_packet = Signal()

    def __init__(self, ext):
        """Constructor.

        Parameters:
            ext (FlockCtrlDronesExtension): the extension that owns this
                manager
        """
        self._ack_collector = None
        self._inbound_thread = None
        self._outbound_thread = None
        self._parser = FlockCtrlParser()
        self._xbee = None

        self.ext = ext

    def app_context(self):
        """Returns the Flask application context of the associated FlockCtrl
        server app. The inbound and outbound XBee green threads will run in
        this application context.
        """
        return self.ext.app.app_context()

    def _handle_inbound_xbee_frame(self, sender, frame):
        """Handles an inbound XBee frame.

        Parameters:
            sender (XBeeOutboundThread): the thread that sent the signal
                that triggered this signal handler
            frame (dict): the inbound XBee frame to handle
        """
        identifier = frame.get("id")
        if identifier == "rx":
            self._handle_inbound_xbee_data_frame(frame)
        elif identifier == "tx_status":
            self._handle_inbound_xbee_transmission_status_frame(frame)
        else:
            self.log.warn("Unhandled XBee packet received; type = {0!r}, "
                          "content = {1!r}".format(identifier, frame))

    def _handle_inbound_xbee_data_frame(self, frame):
        """Handles an inbound XBee data frame.

        Parameters:
            frame (dict): the inbound XBee frame to handle
        """
        assert frame["id"] == "rx"

        data = frame.get("rf_data")
        if not data:
            return

        try:
            packet = self._parser.parse(data)
        except ParseError as ex:
            self.log.warn("Failed to parse FlockCtrl packet of length "
                          "{0}: {1!r}".format(len(data), data[:32]))
            self.log.exception(ex)
            return

        packet.source_address = frame.get("source_addr_long")
        self.on_packet.send(self, packet=packet)

    def _handle_inbound_xbee_transmission_status_frame(self, frame):
        """Handles an inbound XBee transmission status frame.

        Parameters:
            frame (dict): the inbound XBee frame to handle
        """
        assert frame["id"] == "tx_status"
        if self._ack_collector is not None:
            self._ack_collector.notify_status(frame)

    def _handle_xbee_transmission_failure(self, sender, request, status,
                                          should_retry):
        """Handles the failure of an XBee transmission.

        Parameters:
            sender (AcknowledgmentCollector): the acknowledgment collector
                that sent this signal
            request (XBeePacketSendingRequest): the request that failed
            status (int): the delivery status code
            should_retry (bool): whether the status code indicates that the
                transmission should be retried
        """
        if not should_retry:
            message = xbee_delivery_status_code_to_name(status)
            self.log.warn("Failed XBee packet transmission: {0.packet!r}, "
                          "reason = {1}".format(request, message))
        else:
            # Put the request back into the queue after a minor (random)
            # delay. Using a random delay here prevents repeated collisions
            # between messages if they all happen to fail and attempt to
            # re-transmit themselves at the same time. The maximum delay is
            # 50 msec
            spawn_after(random() * 0.05, self._packet_queue.put, request)

    @property
    def log(self):
        """Returns the logger of the extension that owns this manager.

        Returns:
            Optional[logging.Logger]: the logger of the extension that owns
                this manager, or ``None`` if the manager is not associated
                to an extension yet.
        """
        return self.ext.log if self.ext else None

    def send_packet(self, packet, destination):
        """Requests the communication manager to send the given FlockCtrl
        packet to the given destination.

        Parameters:
            packet (FlockCtrlPacket): the packet to send
            destination (Optional[bytes]): the long destination address to
                send the packet to. ``None`` means to send a broadcast
                packet.
        """
        if destination is None:
            destination = _XBEE_BROADCAST_ADDRESS

        req = XBeePacketSendingRequest(
            packet=packet,
            destination=destination,
            needs_acknowledgment=(destination != _XBEE_BROADCAST_ADDRESS)
        )
        self._packet_queue.put(req)

    @property
    def xbee(self):
        """The XBee object that is used to send and receive packets."""
        return self._xbee

    @xbee.setter
    def xbee(self, value):
        if self._xbee == value:
            return

        if self._xbee is not None:
            self._ack_collector.on_failure.disconnect(
                self._handle_xbee_transmission_failure,
                sender=self._ack_collector
            )

            self._inbound_thread.kill()
            self._outbound_thread.kill()

            self._parser.packet_log = None

        self._xbee = value

        if self._xbee is not None:
            self._parser.packet_log = self.log

            self._ack_collector = AcknowledgmentCollector()
            self._ack_collector.on_failure.connect(
                self._handle_xbee_transmission_failure,
                sender=self._ack_collector
            )

            thread = XBeeInboundThread(self, self._xbee)
            thread.on_frame.connect(self._handle_inbound_xbee_frame)
            self._xbee_inbound_thread = spawn(thread.run)

            thread = XBeeOutboundThread(self, self._xbee, self._ack_collector)
            self._packet_queue = thread.queue
            self._xbee_outbound_thread = spawn(thread.run)


class XBeeInboundThread(object):
    """Green thread that reads incoming packets from an XBee serial
    connection and dispatches signals for every one of them.

    The thread is running within the application context of the Flockwave
    server application.
    """

    on_frame = Signal()

    def __init__(self, manager, xbee):
        """Constructor.

        Parameters:
            manager (XBeeCommunicationManager): the communication manager
                that owns this thread
            xbee (ZigBee): the ZigBee object that should be used to wait
                for inbound packets
        """
        self.manager = manager
        self._xbee = xbee

    def _callback(self, frame):
        """Callback function called for every single frame read from the
        XBee.
        """
        self.on_frame.send(self, frame=frame)

    def _error_callback(self, exception):
        """Callback function called when an exception happens while waiting
        for a data frame.
        """
        self.manager.log.exception(exception)

    def run(self):
        """Waits for incoming frames on the associated low-level XBee
        connection and dispatches a signal for every one of them.

        The body of this function is mostly copied from `XBeeBase.run()`.
        Sadly enough, I haven't found a way to prevent XBeeBase_ from
        spawning a new thread on its own when passing a callback to it in
        the constructor.
        """
        with self.manager.app_context():
            while True:
                try:
                    self._callback(self._xbee.wait_read_frame())
                except Exception as ex:
                    self._error_callback(ex)


class XBeeOutboundThread(object):
    """Green thread that sends outbound packets to an XBee serial
    connection. The outbound packets must be placed into a queue that
    is owned by this thread.

    The thread is running within the application context of the Flockwave
    server application.
    """

    def __init__(self, manager, xbee, ack_collector):
        """Constructor.

        Parameters:
            manager (XBeeCommunicationManager): the communication manager
                that owns this thread
            xbee (ZigBee): the ZigBee object that should be used to send
                outbound packets
            ack_collector (AcknowledgmentCollector): the transmit status
                packet collector that should be asked for a frame ID when a
                packet requiring acknowledgment is about to be trasmitted
        """
        self.manager = manager
        self._xbee = xbee
        self._queue = Queue()
        self._ack_collector = ack_collector

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
        thread, and sends each of them via the XBee connection.
        """
        with self.manager.app_context():
            while True:
                try:
                    self._serve_request(self._queue.get())
                except Exception as ex:
                    self._error_callback(ex)

    def _serve_request(self, request):
        """Serves an XBee packet sending request by sending a packet to a
        given destination.

        Parameters:
            request (XBeePacketSendingRequest): the request object containing
                the packet to send, its destination address, and whether the
                packet needs an acknowledgment
        """
        destination = request.destination
        kwds = {
            "dest_addr": b"\xFF\xFE",
            "dest_addr_long": destination,
            "data": request.packet.encode(),
            "options": int2byte(0)
        }
        if destination == _XBEE_BROADCAST_ADDRESS:
            kwds.update(
                broadcast_radius=int2byte(16),
                frame_id=int2byte(0)
            )
        elif request.needs_acknowledgment:
            frame_id = self._ack_collector.wait_for(request)
            kwds["frame_id"] = int2byte(frame_id)

        self._xbee.send("tx", **kwds)


class AcknowledgmentCollector(object):
    """Class that maintains a table of packets waiting to be acknowledged.

    XBee outbound packets may have a frame ID ranging from 1 to 255
    (inclusive). When such a frame ID is provided, the radio module will
    send a transmit status packet that can be used to decide whether the
    transmission was successful or not. This object manages a table mapping
    frame IDs to the transmitted packets that are waiting to be acknowledged,
    and is able to produce a free frame ID (that is not used yet) on-demand.

    Attributes:
        on_failure (Signal): signal that is sent when the acknowledment
            collector recognizes that the sending of a packet corresponding
            to a given request has failed
    """

    on_failure = Signal()

    def __init__(self):
        self._next_frame_id = 1
        self._waiting_for_ack = [None] * 256
        self._status_code_counters = Counter()

    def _get_next_frame_id(self):
        """Returns the next unused frame ID that we can use in an outbound
        packet to request an acknowledgment.

        Returns zero and prints a warning if there are no unused frame IDs
        at the moment; this should never happen because it means that we are
        currently waiting for acknowledgments for at least 255 frames.

        Returns:
            int: the next free frame ID or zero if there are no free frame
                ID slots.
        """
        start = self._next_frame_id
        result = start

        while self._waiting_for_ack[result] is not None:
            result += 1
            if result == 256:
                result = 1
            elif result == start:
                # No free frame ID
                self.manager.log("No free frame ID for outbound XBee "
                                 "packet; this is either a bug or an "
                                 "indication that the XBee bandwidth "
                                 "is saturated.")
                return 0

        return result

    def notify_status(self, packet):
        """Notifies the acknowledgment collector that an XBee transmission
        status frame was received.

        Parameters:
            packet (dict): the received XBee transmission status frame
        """
        frame_id = byte2int(packet.get("frame_id"))

        if frame_id >= 1 and frame_id < 256:
            request = self._waiting_for_ack[frame_id]
            self._waiting_for_ack[frame_id] = None
        else:
            request = None

        if request is None:
            # Stale acknowledgment; we are not interested
            return

        status = byte2int(packet.get("deliver_status", None))
        should_retry = status in (
            0x01,         # MAC ACK failure
            0x02,         # CCA failure
            0x21,         # Network ACK failure
            0x25,         # Route not found
            0x26,         # Broadcast source failed to hear a neighbor relay
            0x2B,         # Invalid binding table index
            0x2C,         # Resource error (lack of free buffers, timers etc)
            0x32          # Resource error (lack of free buffers, timers etc)
        )
        if status != 0:
            self._status_code_counters[status] += 1
            self.on_failure.send(self, request=request, status=status,
                                 should_retry=should_retry)

    def wait_for(self, request):
        """Asks the acknowledgment collector to wait for the acknowledgment
        of a packet that will be sent in response to the given packet sending
        request, and return a frame ID that can be used when the packet is
        sent.

        Parameters:
            request (XBeePacketSendingRequest): the request that initiated
                the sending of the packet

        Returns:
            int: the frame ID that should be used when sending the packet,
                or zero if there are no free frame ID slots.
        """
        frame_id = self._get_next_frame_id()

        self._waiting_for_ack[frame_id] = request
        self._next_frame_id = frame_id + 1
        if self._next_frame_id == 256:
            self._next_frame_id = 1

        return frame_id
