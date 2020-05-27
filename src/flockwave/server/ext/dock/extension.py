from contextlib import ExitStack
from tinyrpc.dispatch import RPCDispatcher
from tinyrpc.protocols.msgpackrpc import MSGPACKRPCProtocol
from trio import open_nursery, sleep_forever
from trio.abc import Stream
from typing import Optional

from flockwave.connections import create_connection, StreamWrapperConnection
from flockwave.channels import MessageChannel
from flockwave.listeners import create_listener
from flockwave.parsers.rpc import RPCMessage
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.object import registered
from flockwave.server.model.uav import PassiveUAVDriver
from flockwave.server.registries import find_in_registry

from ..base import UAVExtensionBase

from .model import Dock, is_dock
from .rpc import DockRPCServer

############################################################################


def create_rpc_message_channel(stream: Stream) -> MessageChannel[RPCMessage]:
    """Creates a unidirectional Trio-style channel that reads data from the
    given Trio stream, and parses incoming RPC messages automatically.

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

        server = DockRPCServer()
        dispatcher = RPCDispatcher()
        dispatcher.register_instance(server)
        dock = None

        self._current_stream = stream

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
                    server.dock = dock
                    server.create_mutator = self.app.device_tree.create_mutator

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

    def handle_DOCK_INF(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles incoming DOCK-INF messages.

        Parameters:
            message: the message that was received
            sender: the client that sent the message
            hub: the message hub that handles the message
        """
        statuses = {}

        dock_ids = message.body["ids"]

        body = {"status": statuses, "type": "DOCK-INF"}
        response = hub.create_response_or_notification(
            body=body, in_response_to=message
        )

        for dock_id in dock_ids:
            dock = self._find_dock_by_id(dock_id, response)
            if dock:
                statuses[dock_id] = dock.status.json

        return response

    async def run(self, app, configuration, logger):
        listener = configuration.get("listener")

        async with open_nursery() as nursery:
            with ExitStack() as stack:
                # Register message handlers for dock-related messages
                stack.enter_context(
                    app.message_hub.use_message_handlers(
                        {"DOCK-INF": self.handle_DOCK_INF}
                    )
                )

                stack.enter_context(registered("dock", Dock))

                # If we have a dedicated listener where the docking station will
                # connect to us, prepare the listener to handle the connections,
                # and add a connection object to the server to represent its
                # status. If there is no listener, we are responsible for
                # handling dock-related messages only
                if listener:
                    listener = create_listener(listener)
                    listener.handler = self.handle_connection_safely
                    listener.nursery = nursery

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
                else:
                    await sleep_forever()

    def _find_dock_by_id(
        self, dock_id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[Dock]:
        """Finds the dock with the given ID in the object registry or registers
        a failure in the given response object if there is no dock with the
        given ID.

        Parameters:
            dock_id: the ID of the dock to find
            response: the response in which the failure can be registered

        Returns:
            the dock with the given ID or ``None`` if there is no such dock
        """
        return find_in_registry(
            self.app.object_registry,
            dock_id,
            predicate=is_dock,
            response=response,
            failure_reason="No such dock",
        )


construct = DockExtension
