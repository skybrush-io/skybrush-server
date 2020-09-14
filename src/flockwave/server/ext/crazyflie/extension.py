"""Extension that adds support for Crazyflie drones."""

from contextlib import contextmanager, ExitStack
from functools import partial
from trio import open_nursery

from flockwave.connections.factory import create_connection
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose

from .connection import CrazyradioConnection

__all__ = ("construct",)


@contextmanager
def registered_connection(cls, name):
    # TODO(ntamas): this can be replaced with create_connection.use() once we
    # upgrade to flockwave-conn 1.13.0
    try:
        create_connection.register(name, cls)
        yield
    finally:
        create_connection.unregister(name)


class CrazyflieDronesExtension(UAVExtensionBase):
    """Extension that adds support for Crazyflie drones."""

    async def run(self, app, configuration):
        connection_config = configuration.get("connections", [])

        # We need a nursery that will be the parent of all tasks that handle
        # Crazyradio connections
        async with open_nursery() as nursery:
            with ExitStack() as stack:
                stack.enter_context(
                    registered_connection(CrazyradioConnection, "crazyradio")
                )

                # Register all the connections and ask the app to supervise them
                for index, spec in enumerate(connection_config):
                    connection = create_connection(spec)
                    if hasattr(connection, "assign_nursery"):
                        connection.assign_nursery(nursery)

                    stack.enter_context(
                        app.connection_registry.use(
                            connection,
                            f"Crazyradio{index}",
                            description=f"Crazyradio connection {index}",
                            purpose=ConnectionPurpose.uavRadioLink,
                        )
                    )

                    nursery.start_soon(
                        partial(
                            app.supervise,
                            connection,
                            task=CrazyradioConnectionHandlerTask.create_and_run,
                        )
                    )


class CrazyradioConnectionHandlerTask:
    """Class responsible for handling a single Crazyradio connection from the
    time it is opened to the time it is closed.
    """

    @classmethod
    async def create_and_run(cls, conn: CrazyradioConnection):
        """Creates and runs a new connection handler for the given radio
        connection.
        """
        await CrazyradioConnectionHandlerTask(conn).run()

    def __init__(self, conn: CrazyradioConnection):
        """Constructor.

        Parameters:
            conn: the connection that the task handles
        """
        self._conn = conn

    async def run(self):
        """Implementation of the task itself."""
        from .scanning import scan_connection

        gen = scan_connection(self._conn)
        while True:
            target = await gen.__anext__()
            # TODO(ntamas): create a drone object
            await gen.asend(True)


construct = CrazyflieDronesExtension
