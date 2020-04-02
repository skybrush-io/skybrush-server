"""Extension that shuts down the server automatically if no clients are
connected for a given number of seconds.
"""

from contextlib import ExitStack
from functools import partial
from math import inf
from trio import CancelScope, current_time, sleep_forever


def on_client_count_changed(cancel_scope, timeout, sender, client=None):
    if sender.num_entries > 0:
        cancel_scope.deadline = inf
    else:
        cancel_scope.deadline = current_time() + timeout


async def run(app, configuration, logger):
    timeout = float(configuration.get("timeout", 300))

    logger.warn(
        f"Server will shut down after {timeout} seconds if there are "
        + "no connected clients"
    )

    with ExitStack() as stack:
        cancel_scope = stack.enter_context(CancelScope())

        handler = partial(on_client_count_changed, cancel_scope, timeout)
        stack.enter_context(app.client_registry.added.connected_to(handler))
        stack.enter_context(app.client_registry.removed.connected_to(handler))

        handler(app.client_registry)

        await sleep_forever()

    logger.warn("Shutting down due to inactivity.")
    app.request_shutdown()
