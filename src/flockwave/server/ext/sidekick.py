"""Extension that handles communication with Skybrush Sidekick, a helper
application that manages a secondary radio channel independently of Skybrush
Server.

This extension is responsible for providing and advertising a service that
Skybrush Sidekick can connect to in order to receive pre-encoded MAVLink
RTK correction packets and other auxiliary status information that it needs.

Note that Skybrush Sidekick can (and *must* be able to) work independently of
Skybrush server; the data provided by this extension is optional and not
required for Skybrush Sidekick to work. In particular, the extension provides:

  * RTK correction packets that Sidekick may weave into its own radio stream

  * a basic summary of status information about MAVLink drones that Skybrush
    Sidekick may use on its own UI to show which drones are active.
"""

from base64 import b64encode
from contextlib import ExitStack
from trio import (
    BrokenResourceError,
    move_on_after,
    open_memory_channel,
    SocketStream,
    WouldBlock,
)
from typing import Any, Optional

from flockwave.encoders.json import create_json_encoder
from flockwave.networking import format_socket_address
from flockwave.server.ports import get_port_number_for_service
from flockwave.server.utils import overridden
from flockwave.server.utils.networking import serve_tcp_and_log_errors


#: Stores the address where the extension is listening for incoming connections
address = None

#: Reference to the app that the extension is loaded in
app = None

#: List of currently open channels to clients
channels = []

#: JSON encoder to use when sending messages to clients
encoder = create_json_encoder()

#: Reference to the logger object that the application allocated to us when the
#: extension was loaded
log = None

#: Cache of most recent status summaries by networks to avoid sending a status
#: summary to clients if it is the same as before
status_summary_cache = {}


def encode_command(type: str, data: Any) -> bytes:
    """Encodes a command type and a corresponding payload into a format that is
    suitable to be sent over the connection to the Sidekick clients.
    """
    return encoder({"type": type, "data": data})


def get_ssdp_location(client_address) -> Optional[str]:
    """Returns the SSDP location descriptor of the Sidekick listener socket.

    Parameters:
        address: when not `None` and we are listening on multiple (or all)
            interfaces, this address is used to pick a reported address that
            is in the same subnet as the given address
    """
    global address
    return (
        format_socket_address(
            address, format="tcp://{host}:{port}", in_subnet_of=client_address
        )
        if address
        else None
    )


async def handle_connection(stream: SocketStream):
    """Handles a connection attempt from a single client."""
    # Invalidate the status summary cache to ensure that the newly connected
    # client gets a full status update. Note that this means that all other
    # connected clients also get a full status update, but as we don't expect
    # many clients to be connected at the same time, this is probably okay.
    status_summary_cache.clear()

    # We need to use a small buffer here for the memory channel. This is because
    # if there is a congestion on the radio link, we don't want to keep many RTK
    # correction packets in the buffer because they quickly become obsolete.
    # On the other hand, the buffer cannot be too small because RTK correction
    # packet requests may come in bursts. The value below seems to be a good
    # middle ground.
    tx_channel, rx_channel = open_memory_channel(32)

    # Keepalive packet
    KEEPALIVE = b"\n"

    # Register this channel so we get RTK correction packets
    channels.append(tx_channel)

    try:
        async with rx_channel:
            while True:
                data = KEEPALIVE
                with move_on_after(5):
                    data = await rx_channel.receive()
                await stream.send_all(data)

    finally:
        channels.remove(tx_channel)


async def handle_connection_safely(stream: SocketStream):
    """Handles a connection attempt from a single client, ensuring that
    exceptions do not propagate through.

    Parameters:
        stream: a Trio socket stream that we can use to communicate with the client
        limit: Trio capacity limiter that ensures that we are not processing
            too many requests concurrently
    """
    client_address = None
    success = True

    try:
        client_address = format_socket_address(stream.socket.getpeername())
        log.info(
            f"Sidekick connection accepted from {client_address}",
            extra={"semantics": "success"},
        )
        return await handle_connection(stream)
    except BrokenResourceError:
        # Client closed connection, this is okay.
        pass
    except Exception as ex:
        # Exceptions raised during a connection are caught and logged here;
        # we do not let the main task itself crash because of them
        log.exception(ex)
        success = False
    finally:
        if success and client_address:
            log.info(f"Sidekick connection from {client_address} closed")


def handle_mavlink_rtk_packet_fragments(sender, messages) -> None:
    """Handles RTK packet fragments emitted as MAVLink packet specifications
    from the MAVLink extension and enqueues it to be sent to all the connected
    clients.

    Enqueueing is non-blocking; if the client cannot keep up with the packet
    flow, the packet will simply be dropped.

    Parameters:
        sender: the MAVLink network that sent the packet specifications;
            currently ignored.
        messages: list of (type, fields) tuples that describe the MAVLink
            messages to be sent from Sidekick
    """
    if not channels:
        return

    # Each message contains a payload of type 'bytes'; we need to encode this
    # with Base64 so it can be sent over the wire in JSON
    encoded_messages = []
    for type, fields in messages:
        if "data" in fields:
            fields = dict(fields)
            fields["data"] = b64encode(fields["data"]).decode("ascii")
        encoded_messages.append((type, fields))

    # Send the correction packet to all connected clients
    send_encoded_command_to_connected_clients(
        encode_command("rtk", encoded_messages), "RTK correction packet"
    )


def handle_mavlink_status_summary_events(sender, summary) -> None:
    """Handles MAVLink drone status summary events from the MAVLink extension
    and enqueues them to be sent to all the connected clients.

    Enqueueing is non-blocking; if the client cannot keep up with the packet
    flow, the packet will simply be dropped.

    The status summary is a list of length 256 where the i-th element is
    `None` if the drone with MAVLink system ID `i` is currently disconnected,
    otherwise it contains the highest (most severe) error code for the drone.
    An error code of zero means that the drone has no errors or events to
    report.

    Parameters:
        sender: the ID of the MAVLink network that sent the status summary
        summary: the status summary
    """
    if not channels:
        return

    old_summary = status_summary_cache.get(sender)
    if old_summary == summary:
        # Status did not change, we can skip notifying the clients
        return

    # Make a copy of the summary; it is a mutable list and the sender will
    # happily keep on mutating it if the status changes
    status_summary_cache[sender] = list(summary)

    # Let's compress the status summary a bit; we are only sending the
    # widest slice that contains non-null elements
    start, end = 0, len(summary)
    while end > 0 and summary[end - 1] is None:
        end -= 1
    while start < end and summary[start] is None:
        start += 1

    encoded_summary = [sender, start]
    encoded_summary.extend(summary[start:end])

    # Send the correction packet to all connected clients
    send_encoded_command_to_connected_clients(
        encode_command("status.v1", encoded_summary), "status summary packet"
    )


def send_encoded_command_to_connected_clients(data: bytes, what: str) -> None:
    """Sends an encoded command to all connected clients, dropping packets for
    slow consumers as necessary and printing warnings during the process if
    needed.
    """
    # Okay, now send the messages and count the number of clients where we needed
    # to drop a packet due to backpressure
    num_dropped = 0
    for channel in channels:
        try:
            channel.send_nowait(data)
        except WouldBlock:
            # Dropping packet
            num_dropped += 1

    # Print a warning if packets were dropped
    if num_dropped > 0:
        log.warn(f"Dropping outbound {what} due to backpressure")


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    host = configuration.get("host", "")
    port = configuration.get("port", get_port_number_for_service("sidekick"))

    address = host, port
    formatted_address = format_socket_address((host, port))

    signals = app.import_api("signals")
    ssdp = app.import_api("ssdp")

    with ExitStack() as stack:
        stack.enter_context(
            overridden(
                globals(),
                address=address,
                app=app,
                channels=[],
                log=logger,
                status_summary_cache={},
            )
        )
        stack.enter_context(
            signals.use(
                {
                    "mavlink:rtk_fragments": handle_mavlink_rtk_packet_fragments,
                    "mavlink:status_summary": handle_mavlink_status_summary_events,
                }
            )
        )
        stack.enter_context(ssdp.use_service("sidekick-server", get_ssdp_location))

        logger.info(
            f"Listening for Skybrush Sidekick connections on {formatted_address}"
        )

        try:
            # (host or None) is needed below because an empty string as the
            # hostname is not okay on Linux
            await serve_tcp_and_log_errors(
                handle_connection_safely, port, host=(host or None), log=log
            )
        finally:
            logger.info(f"Skybrush Sidekick socket closed on {formatted_address}")


dependencies = ("ssdp", "signals")
description = "Communication channel to Skybrush Sidekick"
schema = {
    "properties": {
        "host": {
            "type": "string",
            "title": "Host",
            "description": (
                "IP address of the host that the server should listen for incoming "
                "connections from Skybrush Sidekick. Use an empty string to listen "
                "on all interfaces, or 127.0.0.1 to listen on localhost only"
            ),
            "default": "",
            "propertyOrder": 10,
        },
        "port": {
            "type": "integer",
            "title": "Port",
            "description": (
                "Port that the server should listen on. Untick the checkbox to "
                "let the server derive the port number from its own base port."
            ),
            "minimum": 1,
            "maximum": 65535,
            "default": get_port_number_for_service("sidekick"),
            "required": False,
            "propertyOrder": 20,
        },
    }
}
