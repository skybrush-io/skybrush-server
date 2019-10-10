"""Connection class that wraps a Trio bidirectional byte stream."""

from abc import abstractmethod
from trio.abc import Stream
from typing import Callable, Optional

from .base import ConnectionBase, ReadableConnection, WritableConnection


class StreamConnectionBase(
    ConnectionBase, ReadableConnection[bytes], WritableConnection[bytes]
):
    """Connection class that wraps a Trio bidirectional byte stream."""

    def __init__(self):
        """Constructor.

        Parameters:
            factory: async callable that must be called with no arguments
                and that will construct a new Trio bidirectional byte
                stream that the connection will wrap.
        """
        super().__init__()
        self._stream = None

    @abstractmethod
    async def _create_stream(self) -> Stream:
        """Creates the stream that the connection should operate on.

        Each invocation of this method should return a new Trio stream
        instance.
        """
        raise NotImplementedError

    async def _open(self):
        """Opens the stream."""
        self._stream = await self._create_stream()

    async def _close(self):
        """Closes the stream."""
        await self._stream.aclose()
        self._stream = None

    async def read(self, size: Optional[int] = None) -> bytes:
        """Reads some data from the stream.

        Parameters:
            size: maximum number of bytes to receive. Must be greater than
                zero. Optional; if omitted, then the stream object is free to
                pick a reasonable default.
        """
        return await self._stream.receive_some(size)

    async def write(self, data: bytes) -> None:
        """Writes some data to the stream.

        The function will block until all the data has been sent.
        """
        await self._stream.send_all(data)


class StreamConnection(StreamConnectionBase):
    """Connection class that wraps a Trio bidirectional byte stream that is
    constructed on-demand from a factory function.
    """

    def __init__(self, factory: Callable[[], Stream]):
        """Constructor.

        Parameters:
            factory: async callable that must be called with no arguments
                and that will construct a new Trio bidirectional byte
                stream that the connection will wrap.
        """
        super().__init__()
        self._factory = factory

    @abstractmethod
    async def _create_stream(self) -> Stream:
        """Creates the stream that the connection should operate on.

        Each invocation of this method should return a new Trio stream
        instance.
        """
        return await self._factory()
