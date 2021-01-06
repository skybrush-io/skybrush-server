"""Skybrush server extension that adds debugging tools and a test page to
the Skybrush server.
"""

import threading

from base64 import b64decode, b64encode
from contextlib import ExitStack
from functools import partial
from math import inf
from operator import attrgetter
from quart import Blueprint, render_template
from trio import (
    BrokenResourceError,
    fail_after,
    open_memory_channel,
    open_nursery,
    serve_tcp,
    TooSlowError,
)
from trio.abc import ReceiveChannel, Stream
from trio.lowlevel import current_root_task
from typing import Callable

from flockwave.networking import format_socket_address
from flockwave.server.utils import overridden

__all__ = ("index", "run")


blueprint = Blueprint(
    "debug",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)

connected_client_queue = None
log = None


async def run(app, configuration, logger):
    """Runs the extension."""
    http_server = app.import_api("http_server")
    path = configuration.get("route", "/debug")
    host = configuration.get("host", "localhost")
    port = configuration.get("port")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), log=logger))
        stack.enter_context(http_server.mounted(blueprint, path=path))
        stack.enter_context(
            http_server.proposed_index_page("debug.index", priority=-100)
        )

        if port is not None:
            on_message = setup_debugging_server(app, stack, debug_clients=True)
            await run_debug_port(host, port, on_message=on_message)


def setup_debugging_server(app, stack, debug_clients: bool = False):
    debug_request_signal = app.import_api("signals").get("debug:request")
    debug_response_signal = app.import_api("signals").get("debug:response")

    # Buffer in which we assemble debug messages to send to the client. It is
    # assumed that debug messages are terminated by \n, optionally preceded by
    # \r
    buffer = []

    def send_debug_message_to_client(data: bytes) -> None:
        nonlocal buffer

        while data:
            pre, sep, data = data.partition(b"\n")
            if pre:
                buffer.append(pre)

            if sep and buffer:
                # We have assembled a full message so we must send it
                merged = b"".join(buffer).rstrip(b"\r")
                if merged:
                    encoded = b64encode(bytes(b ^ 0x55 for b in merged)).decode("ascii")
                    buffer.clear()
                    msg = app.message_hub.create_notification(
                        {"type": "X-DBG-REQ", "data": encoded}
                    )
                    app.message_hub.enqueue_message(msg)

    def handle_debug_response_from_client(message, sender, hub) -> bool:
        data = message.get("data")
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


#############################################################################
# Functions related to handling the dedicated debug port


async def run_debug_port(
    host: str, port: int, on_message: Callable[[bytes], None]
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
    """
    address = host, port
    log.info(f"Starting debug listener on {format_socket_address(address)}...")
    try:
        await serve_tcp(
            partial(handle_debug_connection_safely, on_message=on_message),
            port,
            host=host,
        )
    finally:
        log.info("Debug listener closed.")


async def handle_debug_connection_safely(
    stream: Stream, *, on_message: Callable[[bytes], None]
) -> None:
    """Handles a single debug connection, catching all exceptions so they
    don't propagate out and crash the extension.
    """
    try:
        await handle_debug_connection_outbound(stream, on_message=on_message)
    except BrokenResourceError:
        # THis is OK.
        pass
    except Exception:
        log.exception("Unexpected exception caught while handling debug connection")


async def handle_debug_connection_outbound(
    stream: Stream, *, on_message: Callable[[bytes], None]
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
        with overridden(globals(), connected_client_queue=tx_queue):
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
                        print("Trying to send keepalive")
                        handle_debug_response(b".\r\n")
                        data = None

                    if data:
                        try:
                            on_message(data)
                        except Exception:
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

    connected_client_queue.send_nowait(data)


#############################################################################
# Functions related to handling the dedicated debug port


@blueprint.route("/")
async def index():
    """Returns the index page of the extension."""
    return await render_template("index.html")


@blueprint.route("/threads")
async def list_threads():
    """Returns a page that lists all active threads in the server."""
    data = {"threads": threading.enumerate()}
    return await render_template("threads.html", **data)


@blueprint.route("/tasks")
async def list_tasks():
    """Returns a page that lists all active Trio tasks in the server."""

    tasks = []
    queue = [(0, current_root_task())]
    while queue:
        level, task = queue.pop()
        tasks.append(("    " * level, task))
        for nursery in task.child_nurseries:
            queue.extend(
                (level + 1, task)
                for task in sorted(
                    nursery.child_tasks, key=attrgetter("name"), reverse=True
                )
            )

    return await render_template("tasks.html", tasks=tasks)


dependencies = ("http_server", "signals")
