"""Extension to impose limits on the number of concurrent connections and
on the maximum duration of a connection.
"""

from contextlib import ExitStack
from trio import current_time, sleep
from trio_util import periodic
from weakref import WeakKeyDictionary

from flockwave.server.utils import overridden


app = None
deadlines = None
limits = None, None, None
log = None


async def check_client_auths_in_time(app, client_id: str, deadline: float) -> None:
    """Async task that checks whether the client with the given ID authenticates
    to the server in time.
    """
    global log

    try:
        await sleep(deadline)
        if not is_client_authenticated_or_gone(app, client_id):
            try:
                client = app.client_registry[client_id]
            except KeyError:
                # Client gone, this is okay.
                pass
            await disconnect_client_safely(
                app, client, "Failed to authenticate in time"
            )
    except Exception as ex:
        # Exceptions raised are caught and logged here; we do not let the main
        # task itself crash because of them
        log.exception(ex)


async def disconnect_client_safely(app, client, reason):
    """Disconnects the given client from the app safely, ensuring that
    exceptions do not propagate out from this function.
    """
    global log

    try:
        await app.disconnect_client(client, reason=reason)
    except Exception as ex:
        # Exceptions raised during disconnection are caught and logged here;
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


def is_client_authenticated_or_gone(app, client_id: str) -> bool:
    """Returns whether the client with the given ID is authenticated or gone."""
    try:
        client = app.client_registry[client_id]
    except KeyError:
        # Client gone, this is okay.
        return True
    return client.user is not None


def on_client_added(sender, client):
    global app, deadlines, limits

    if not client:
        return

    max_clients, max_duration, auth_deadline = limits

    if max_clients is not None and max_clients > 0 and sender.num_entries > max_clients:
        # Disconnect the client immediately as there are too many connected
        # clients
        app.run_in_background(
            disconnect_client_safely,
            app,
            client,
            "Too many connected clients; please try again later",
        )

    # Record the time when the client should be disconnected
    if max_duration is not None and max_duration > 0:
        deadlines[client] = current_time() + max_duration

    # If there is a deadline for authentication, run a separate task to test
    # whether the client authenticated in time
    if auth_deadline is not None and auth_deadline > 0:
        app.run_in_background(check_client_auths_in_time, app, client.id, auth_deadline)


def on_client_removed(sender, client):
    global deadlines

    try:
        del deadlines[client]
    except KeyError:
        # Client had no deadline; this may happen if max_duration is None
        pass


async def run(app, configuration, logger):
    auth_deadline = float(configuration.get("auth_deadline", 0))
    max_clients = int(configuration.get("max_clients", 0))
    max_duration = float(configuration.get("max_duration", 0))

    if max_clients > 0:
        logger.warn(f"Allowing {max_clients} concurrent client connection(s)")

    if max_duration > 0:
        logger.warn(
            f"A single client can be connected for at most {max_duration} second(s)"
        )

    if auth_deadline > 0:
        logger.warn(
            f"Clients must authenticate in {auth_deadline} second(s) after login"
        )

    if max_clients <= 0 and max_duration <= 0 and auth_deadline <= 0:
        return

    limits = max_clients, max_duration, auth_deadline
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
