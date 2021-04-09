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

from contextlib import ExitStack
from trio import SocketStream
from typing import Optional

from flockwave.encoders.json import create_json_encoder
from flockwave.networking import format_socket_address
from flockwave.server.ports import get_port_number_for_service
from flockwave.server.utils import overridden
from flockwave.server.utils.networking import serve_tcp_and_log_errors


address = None
app = None
encoder = create_json_encoder()
log = None


def log_encoded_rtk(sender, packet):
    print(repr(packet))


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
    await stream.send_all(b"Hello!\n")


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
        client_address = format_socket_address(stream.socket)
        log.info(
            f"Sidekick connection accepted from {client_address}",
            extra={"semantics": "success"},
        )
        return await handle_connection(stream)
    except Exception as ex:
        # Exceptions raised during a connection are caught and logged here;
        # we do not let the main task itself crash because of them
        log.exception(ex)
        success = False
    finally:
        if success and client_address:
            log.info(f"Sidekick connection from {client_address} closed")


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    host = configuration.get("host", "")
    port = configuration.get("port", get_port_number_for_service("sidekick"))

    address = host, port
    formatted_address = format_socket_address((host, port))

    signals = app.import_api("signals")
    ssdp = app.import_api("ssdp")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), address=address, app=app, log=logger))
        stack.enter_context(signals.use({"mavlink:encoded_rtk": log_encoded_rtk}))
        stack.enter_context(ssdp.use_service("sidekick-server", get_ssdp_location))

        logger.info(
            f"Listening for Skybrush Sidekick connections on {formatted_address}"
        )

        try:
            await serve_tcp_and_log_errors(
                handle_connection_safely, port, host=host, log=log
            )
        finally:
            logger.info(f"Skybrush Sidekick socket closed on {formatted_address}")


dependencies = ("ssdp", "signals")
