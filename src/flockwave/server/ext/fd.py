"""Extension that provides a simple file descriptor based communication
channel between the server and a single client.
"""

from contextlib import ExitStack
from fcntl import fcntl, F_GETFL, F_SETFL
from functools import partial
from os import O_NONBLOCK
from trio import CapacityLimiter, ClosedResourceError, Lock, open_file, open_nursery
from trio.lowlevel import FdStream

from flockwave.channels import ParserChannel
from flockwave.encoders.json import create_json_encoder
from flockwave.parsers.json import create_json_parser
from flockwave.server.model import CommunicationChannel
from flockwave.server.utils import overridden


app = None
encoder = create_json_encoder()
log = None
path = None


async def open_fd(fd, mode):
    flag = fcntl(fd, F_GETFL)
    fcntl(fd, F_SETFL, flag | O_NONBLOCK)
    return await open_file(fd, mode)


class FDChannel(CommunicationChannel):
    """Object that represents a file descriptor based communication channel
    between a server and a single client.
    """

    def __init__(self):
        """Constructor."""
        self.out_fp = None

        self._lock = Lock()
        self._nursery = None

    def bind_to(self, client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("fd:"):
            self.in_fd, self.out_fd = [int(x) for x in client.id[3:].split(",")]
        else:
            raise ValueError("client has no ID yet")

    async def close(self, force: bool = False):
        if self._nursery:
            self._nursery.cancel_scope.cancel()
            self._nursery = None

    async def send(self, message):
        """Inherited."""
        async with self._lock:
            # Locking is needed, otherwise we could be running into problems
            # if a message was sent only partially but the message hub is
            # already trying to send another one (since the message hub
            # dispatches each message in a separate task)
            await self.out_fp.write(encoder(message))
            await self.out_fp.flush()

    async def serve(self, handler):
        async with open_nursery() as self._nursery:
            try:
                await self._serve(handler)
            except Exception as ex:
                log.exception(ex)
                log.error("Closing connection")
            finally:
                self.cancel_scope = None

    async def _serve(self, handler):
        nursery = self._nursery

        async with await open_fd(self.out_fd, "wb") as self.out_fp:
            async with FdStream(self.in_fd) as stream:
                parser = create_json_parser()
                channel = ParserChannel(reader=stream.receive_some, parser=parser)
                try:
                    async for line in channel:
                        nursery.start_soon(handler, line)
                except ClosedResourceError:
                    # This is okay.
                    pass


############################################################################


async def handle_message(message, client, limit: CapacityLimiter) -> None:
    """Handles a single message received from the given sender.

    Parameters:
        message: the incoming message, waiting to be parsed
        client: the client that sent the message
    """
    async with limit:
        await app.message_hub.handle_incoming_message(message, client)


############################################################################


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    in_fd = int(configuration.get("in", 0))
    out_fd = int(configuration.get("out", 1))

    if in_fd == out_fd:
        raise ValueError("file descriptors must be different")

    client_id = f"fd:{in_fd},{out_fd}"

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger, path=path))
        stack.enter_context(app.channel_type_registry.use("fd", factory=FDChannel))

        client = stack.enter_context(app.client_registry.use(client_id, "fd"))

        limit = CapacityLimiter(256)
        handler = partial(handle_message, client=client, limit=limit)

        await client.channel.serve(handler)
