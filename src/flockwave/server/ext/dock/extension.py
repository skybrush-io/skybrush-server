from contextlib import ExitStack
from trio import open_nursery, sleep, sleep_forever

from flockwave.connections import create_connection
from flockwave.listeners import create_listener
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.uav import PassiveUAVDriver

from ..base import UAVExtensionBase

############################################################################


class DockExtension(UAVExtensionBase):
    """Extension that implements support for CollMot Robotics' docking station."""

    def __init__(self):
        """Constructor."""
        super(DockExtension, self).__init__()
        self._connection = create_connection("dummy")
        self._id_format = None
        self._device_to_uav_id = {}

    def _create_driver(self):
        return PassiveUAVDriver()

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "DOCK:{0}")

    async def handle_connection(self, stream):
        """Handles a connection attempt from a single client.

        Parameters:
            stream (SocketStream): a Trio socket stream that we can use to
                communicate with the client
        """
        await sleep(10)

    async def handle_connection_safely(self, stream):
        """Handles a connection attempt from a single client, ensuring
        that exceptions do not propagate through.

        Parameters:
            stream (SocketStream): a Trio socket stream that we can use to
                communicate with the client
        """
        # TODO(ntamas): ensure that we only allow a single connection
        try:
            async with self._connection:
                return await self.handle_connection(stream)
        except Exception as ex:
            # Exceptions raised during a connection are caught and logged here;
            # we do not let the main task itself crash because of them
            self.log.exception(ex)

    async def run(self, app, configuration, logger):
        listener = configuration.get("listener")
        if not listener:
            logger.warn("No listener specified; dock extension disabled")
            return

        listener = create_listener(listener)
        listener.handler = self.handle_connection_safely

        with ExitStack() as stack:
            stack.enter_context(
                app.connection_registry.use(
                    self._connection,
                    "Dock",
                    "Docking station",
                    # TODO(ntamas): use ConnectionPurpose.dock
                    purpose=ConnectionPurpose.other,
                )
            )

            async with open_nursery() as nursery:
                listener.nursery = nursery
                async with listener:
                    await sleep_forever()


async def run(app, configuration, logger):
    pass


construct = DockExtension
