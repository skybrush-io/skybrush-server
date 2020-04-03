"""Extension to impose limits on the number of concurrent connections and
on the maximum duration of a connection.
"""

from contextlib import ExitStack
from trio import current_time
from trio_util import periodic
from weakref import WeakKeyDictionary

from flockwave.server.utils import overridden


app = None
deadlines = None
limits = None, None
log = None


async def disconnect_client_safely(app, client, reason):
    """Disconnects the given client from the app safely, ensuring that
    exceptions do not propagate out from this function.
    """
    global log

    try:
        await app.disconnect_client(client, reason=reason)
    except Exception as ex:
        # Exceptions raised during a connection are caught and logged here;
        # we do not let the main task itself crash because of them
        log.exception(ex)


def disconnect_clients_if_needed():
    """Iterates over all clients with a registered deadline and disconnects
    those that need to be disconnected.
    """
    global app

    now = current_time()
    to_disconnect = [client for client, deadline in deadlines.items() if deadline < now]
    for client in to_disconnect:
        app.run_in_background(disconnect_client_safely, app, client, "Session expired")


def on_client_added(sender, client):
    global app, deadlines, limits

    if not client:
        return

    max_clients, max_duration = limits

    if sender.num_entries > max_clients:
        # Disconnect the client immediately as there are too many connected
        # clients
        app.run_in_background(
            disconnect_client_safely,
            app,
            client,
            "Too many connected clients; please try again later",
        )

    # Record the time when the client should be disconnected
    deadlines[client] = current_time() + max_duration


def on_client_removed(sender, client):
    global deadlines

    del deadlines[client]


async def run(app, configuration, logger):
    max_clients = int(configuration.get("max_clients", 0))
    max_duration = float(configuration.get("max_duration", 0))

    if max_clients > 0:
        logger.warn(f"Allowing {max_clients} concurrent client connection(s)")

    if max_duration > 0:
        logger.warn(
            f"A single client can be connected for at most {max_duration} second(s)"
        )

    if max_clients <= 0 and max_duration <= 0:
        return

    limits = max_clients, max_duration
    deadlines = WeakKeyDictionary()
    check_period = 1 if max_duration < 60 else 10 if max_duration < 300 else 60

    with ExitStack() as stack:
        stack.enter_context(
            overridden(
                globals(), app=app, deadlines=deadlines, limits=limits, log=logger
            )
        )

        stack.enter_context(app.client_registry.added.connected_to(on_client_added))
        stack.enter_context(app.client_registry.removed.connected_to(on_client_removed))

        async for _ in periodic(check_period):
            try:
                disconnect_clients_if_needed()
            except Exception as ex:
                log.exception(ex)
