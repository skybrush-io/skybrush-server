"""Functions related to handling the dedicated debug port"""

from base64 import b64decode, b64encode
from functools import partial
from logging import Logger
from math import inf
from trio import (
    BrokenResourceError,
    fail_after,
    open_memory_channel,
    open_nursery,
    TooSlowError,
)
from trio.abc import ReceiveChannel, Stream
from typing import Callable, Optional

from flockwave.networking import format_socket_address
from flockwave.server.utils import overridden
from flockwave.server.utils.networking import serve_tcp_and_log_errors

__all__ = ("setup_debugging_server",)


#: Buffer in which we assemble debug messages to send to the client. It is
#: assumed that debug messages are terminated by \n, optionally preceded by
#: \r
buffer = []

connected_client_queue = None


def setup_debugging_server(app, stack, debug_clients: bool = False):
    debug_request_signal = app.import_api("signals").get("debug:request")
    debug_response_signal = app.import_api("signals").get("debug:response")

    def send_debug_message_to_client(data: bytes) -> None:
        global buffer

        while data:
            pre, sep, data = data.partition(b"\n")
            if pre:
                buffer.append(pre)

            if sep and buffer:
                # We have assembled a full message so we must send it
                merged = b"".join(buffer).rstrip(b"\r")
                if merged:
                    encoded = b64encode(bytes(b ^ 0x55 for b in merged)).decode("ascii")
                    msg = app.message_hub.create_notification(
                        {"type": "X-DBG-REQ", "data": encoded}
                    )
                    app.message_hub.enqueue_message(msg)
                buffer.clear()

    def handle_debug_response_from_client(message, sender, hub) -> bool:
        data = message.body.get("data")
        if data:
            try:
                data = bytes(b ^ 0x55 for b in b64decode(data.encode("ascii")))
            except Exception:
                data = None

            if data:
                debug_response_signal.send(data)

        return hub.acknowledge(message)

    if debug_clients:
        stack.enter_context(
            app.message_hub.use_message_handlers(
                {"X-DBG-RESP": handle_debug_response_from_client}
            )
        )
        stack.enter_context(
            debug_request_signal.connected_to(send_debug_message_to_client)
        )

    stack.enter_context(debug_response_signal.connected_to(handle_debug_response))

    return debug_request_signal.send


async def run_debug_port(
    host: str,
    port: int,
    on_message: Callable[[bytes], None],
    log: Optional[Logger] = None,
) -> None:
    """Opens a TCP port that can be used during debugging to inject arbitrary
    data into data streams provided by other extensions if they support this
    debugging interface.

    Currently this is developed on an ad-hoc basis as we need it. Do not use
    this feature in production.

    Parameters:
        host: the hostname or IP address where the port should be opened
        port: the port number
        on_message: the function to call when an incoming data chunk is received
        log: optional logger to log messages to
    """
    address = host, port

    if log:
        log.info(f"Starting debug listener on {format_socket_address(address)}...")

    try:
        await serve_tcp_and_log_errors(
            partial(handle_debug_connection_safely, on_message=on_message, log=log),
            port,
            host=host,
            log=log,
        )
    finally:
        if log:
            log.info("Debug listener closed.")


async def handle_debug_connection_safely(
    stream: Stream, *, on_message: Callable[[bytes], None], log: Optional[Logger] = None
) -> None:
    """Handles a single debug connection, catching all exceptions so they
    don't propagate out and crash the extension.
    """
    try:
        await handle_debug_connection_outbound(stream, on_message=on_message, log=log)
    except BrokenResourceError:
        # This is OK.
        pass
    except Exception:
        if log:
            log.exception("Unexpected exception caught while handling debug connection")


async def handle_debug_connection_outbound(
    stream: Stream, *, on_message: Callable[[bytes], None], log: Optional[Logger] = None
) -> None:
    if connected_client_queue is not None:
        # one connection only
        await stream.aclose()
        return

    # Using an infinite queue that can be used to send data to the connected
    # client. We don't know in advance how much debug data we can expect, but
    # sometimes it's a lot and we don't have a way to communicate backpressure
    # via signals so it's better to use an unbounded queue. It is not to be used
    # in production anyway.
    tx_queue, rx_queue = open_memory_channel(inf)

    async with tx_queue:
        with overridden(globals(), connected_client_queue=tx_queue, buffer=[]):
            async with open_nursery() as nursery:
                nursery.start_soon(handle_debug_connection_inbound, stream, rx_queue)

                while True:
                    try:
                        with fail_after(30):
                            data = await stream.receive_some()
                            if not data:
                                # Connection closed
                                break
                    except TooSlowError:
                        # no data from client in 30 seconds, send a keepalive packet
                        handle_debug_response(b".")
                        data = None

                    if data:
                        try:
                            on_message(data)
                        except Exception:
                            if log:
                                log.exception(
                                    "Unexpected exception while executing debug message handler"
                                )

                nursery.cancel_scope.cancel()


async def handle_debug_connection_inbound(
    stream: Stream, queue: ReceiveChannel
) -> None:
    """Handles inbound messages sent from other components in the server that
    should be dispatched to the currently connected client of the debug port.
    """
    async for data in queue:
        await stream.send_all(data)


def handle_debug_response(data: bytes) -> None:
    """Handler that is called when another part of the server wishes to send
    a message to the client currently connected to the debug port.
    """
    if connected_client_queue is None:
        # No client connected, dropping debug message silently.
        return

    connected_client_queue.send_nowait(data + b"\r\n")
