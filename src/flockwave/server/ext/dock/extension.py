from contextlib import ExitStack
from functools import partial
from tinyrpc import InvalidRequestError
from tinyrpc.dispatch import RPCDispatcher
from tinyrpc.protocols import RPCRequest
from tinyrpc.protocols.jsonrpc import JSONRPCProtocol
from trio import ClosedResourceError, open_memory_channel, open_nursery, sleep_forever
from trio.abc import ReceiveChannel, SendChannel, Stream

from flockwave.connections import create_connection
from flockwave.channels import ParserChannel
from flockwave.encoders.rpc import create_rpc_encoder
from flockwave.listeners import create_listener
from flockwave.logger import Logger
from flockwave.parsers.rpc import create_rpc_parser, RPCMessage
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.uav import PassiveUAVDriver

from ..base import UAVExtensionBase

from .rpc import DockRPCServer

############################################################################


def create_rpc_message_parser_channel(
    stream: Stream, log: Logger
) -> ParserChannel[RPCMessage]:
    """Creates a unidirectional Trio-style channel that reads data from the
    given Trio stream, and parses incoming JSON-RPC messages automatically.

    Parameters:
        stream: the stream to read data from
        log: the logger on which any error messages and warnings should be logged
    """
    rpc_parser = create_rpc_parser(protocol=JSONRPCProtocol())
    return ParserChannel(stream.receive_some, parser=rpc_parser)


############################################################################


class DockExtension(UAVExtensionBase):
    """Extension that implements support for CollMot Robotics' docking station."""

    def __init__(self):
        """Constructor."""
        super(DockExtension, self).__init__()
        self._connection = create_connection("dummy")
        self._id_format = None
        self._device_to_uav_id = {}

        self._dispatcher = RPCDispatcher()
        self._dispatcher.register_instance(DockRPCServer())

        self._current_stream = None
        self._send_message = None

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

        try:
            self._current_stream = stream
            queue_tx, queue_rx = open_memory_channel(0)

            async with open_nursery() as nursery:
                nursery.start_soon(self.handle_outbound_messages, stream, queue_rx)
                nursery.start_soon(self.handle_inbound_messages, stream, queue_tx)
        except Exception as ex:
            # Exceptions raised during a connection are caught and logged here;
            # we do not let the main task itself crash because of them
            self.log.exception(ex)
        finally:
            self._current_stream = None

    async def handle_inbound_messages(self, stream: Stream, queue: SendChannel):
        """Task that handles the inbound messages from the given stream."""
        channel = create_rpc_message_parser_channel(stream, queue)
        try:
            async with queue:
                async for message in channel:
                    if isinstance(message, RPCRequest):
                        # TODO(ntamas): do it in a separate task
                        response = self._dispatcher.dispatch(message)
                        if response and not message.one_way:
                            await queue.send(response)
                    elif isinstance(message, list):
                        self.log.warn(
                            "Batched requests not supported; dropping message"
                        )
                    else:
                        # TODO(ntamas): handle responses
                        self.log.warn(
                            "Only RPC requests are supported; dropping message"
                        )

        except InvalidRequestError:
            self.log.error("Invalid RPC request, closing connection.")
            await stream.aclose()

    async def handle_outbound_messages(self, stream: Stream, queue: ReceiveChannel):
        """Task that handles the sending of outbound messages to the given
        stream.
        """
        encoder = create_rpc_encoder(protocol=JSONRPCProtocol())
        try:
            async with queue:
                async for message in queue:
                    await stream.send_all(encoder(message))
        except ClosedResourceError:
            # Stream closed, this is OK
            pass

    async def run(self, app, configuration, logger):
        listener = configuration.get("listener")
        if not listener:
            logger.warn("No listener specified; dock extension disabled")
            return

        async with open_nursery() as nursery:
            listener = create_listener(listener)
            listener.handler = partial(self.handle_connection_safely)
            listener.nursery = nursery

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

                async with listener:
                    await sleep_forever()


construct = DockExtension
