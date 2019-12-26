from contextlib import ExitStack
from tinyrpc.dispatch import RPCDispatcher
from tinyrpc.protocols.msgpackrpc import MSGPACKRPCProtocol
from trio import open_nursery, sleep_forever
from trio.abc import Stream

from flockwave.connections import create_connection, StreamWrapperConnection
from flockwave.channels import MessageChannel
from flockwave.listeners import create_listener
from flockwave.parsers.rpc import RPCMessage
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.object import registered
from flockwave.server.model.uav import PassiveUAVDriver

from ..base import UAVExtensionBase

from .model import Dock
from .rpc import DockRPCServer

############################################################################


def create_rpc_message_channel(stream: Stream) -> MessageChannel[RPCMessage]:
    """Creates a unidirectional Trio-style channel that reads data from the
    given Trio stream, and parses incoming JSON-RPC messages automatically.

    Parameters:
        stream: the stream to read data from
        log: the logger on which any error messages and warnings should be logged
    """
    connection = StreamWrapperConnection(stream)
    return MessageChannel.for_rpc_protocol(MSGPACKRPCProtocol(), connection)


############################################################################


class DockExtension(UAVExtensionBase):
    """Extension that implements support for CollMot Robotics' docking station."""

    def __init__(self):
        """Constructor."""
        super(DockExtension, self).__init__()

        self._connection = create_connection("dummy")

        self._id_format = None
        self._current_stream = None

    def _create_driver(self):
        return PassiveUAVDriver()

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "DOCK:{0}")

    async def handle_connection_safely(self, stream: Stream):
        """Handles a connection attempt from a single client, ensuring
        that exceptions do not propagate through.

        Parameters:
            stream: a Trio socket stream that we can use to communicate with the
                client
        """
        if self._current_stream is not None:
            self.log.warn(
                "Only one connection is supported; rejecting connection attempt"
            )
            return

        self._current_stream = stream

        dispatcher = RPCDispatcher()
        dispatcher.register_instance(DockRPCServer())
        dock = None

        try:
            channel = create_rpc_message_channel(stream)
            async with self._connection:
                async with channel.serve_rpc_requests(
                    handler=dispatcher.dispatch, log=self.log
                ) as peer:
                    # Get the unique ID of the dock so we can register it
                    uid = await peer.request.getUid()

                    # Register the dock as an object
                    dock = Dock(self._id_format.format(uid))
                    self.log.info("Connected to docking station {0}".format(dock.id))
                    with self.app.object_registry.use(dock):
                        # TODO(ntamas): get initial state here
                        await sleep_forever()
        except Exception as ex:
            # Exceptions raised during a connection are caught and logged here;
            # we do not let the main task itself crash because of them
            self.log.exception(ex)
        finally:
            if dock is not None:
                self.log.info("Disconnected from docking station {0}".format(dock.id))

            self._current_stream = None

    async def run(self, app, configuration, logger):
        listener = configuration.get("listener")
        if not listener:
            logger.warn("No listener specified; dock extension disabled")
            return

        async with open_nursery() as nursery:
            listener = create_listener(listener)
            listener.handler = self.handle_connection_safely
            listener.nursery = nursery

            with ExitStack() as stack:
                stack.enter_context(registered("dock", Dock))
                stack.enter_context(
                    app.connection_registry.use(
                        self._connection,
                        "Dock",
                        "Docking station",
                        purpose=ConnectionPurpose.dock,
                    )
                )

                async with listener:
                    await sleep_forever()


construct = DockExtension
