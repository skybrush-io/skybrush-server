"""Class that implements a Trio-style channel object that takes data from a
ReadableConnection_ and yields parsed message objects.
"""

from collections import deque
from inspect import iscoroutinefunction
from trio import EndOfChannel
from trio.abc import Channel, ReceiveChannel, SendChannel
from typing import Awaitable, Callable, List, TypeVar, Union

from .connections import Connection, ReadableConnection, WritableConnection

__all__ = ("ParserChannel",)

RawType = TypeVar("RawType")
MessageType = TypeVar("MessageType")

Reader = Union[Callable[[], Awaitable[RawType]], ReadableConnection[RawType]]
Writer = Union[Callable[[RawType], None], WritableConnection[RawType]]

Parser = Callable[[RawType], List[MessageType]]
Encoder = Callable[[MessageType], RawType]


class ParserChannel(ReceiveChannel[MessageType]):
    """Trio-style ReceiveChannel_ that takes data from a ReadableConnection_
    and yields parsed message objects.
    """

    def __init__(self, reader: Reader[RawType], parser: Parser[RawType, MessageType]):
        if iscoroutinefunction(getattr(reader, "read", None)):
            # Reader is a Connection
            self._reader = reader.read
            self._closer = getattr(reader, "aclose", None)
        elif iscoroutinefunction(reader):
            self._reader = reader
            self._closer = None
        else:
            raise TypeError(
                f"ReadableConnection or async function expected, got {type(reader)}"
            )

        if callable(getattr(parser, "feed", None)):
            self._parser = parser.feed
        elif callable(parser):
            self._parser = parser
        else:
            raise TypeError(f"Parser or callable expected, got {type(parser)}")

        self._pending = deque()

    async def aclose(self) -> None:
        if self._closer:
            await self._closer()

    async def receive(self) -> MessageType:
        while not self._pending:
            await self._read()
        return self._pending.popleft()

    async def _read(self) -> None:
        """Reads the pending bytes using the associated reader function and
        feeds the parsed messages into the pending list.

        Raises:
            EndOfChannel: if there is no more data to read from the connection
        """
        data = await self._reader()
        if not data:
            raise EndOfChannel()
        self._pending.extend(self._parser(data))


class EncoderChannel(SendChannel[MessageType]):
    """Trio-style SendChannel_ that encodes objects and writes them to a
    WritableConnection_.
    """

    def __init__(self, writer: Writer[RawType], encoder: Encoder[MessageType, RawType]):
        if iscoroutinefunction(getattr(writer, "write", None)):
            # Writer is a Connection
            self._writer = writer.writer
            self._closer = getattr(writer, "aclose", None)
        elif iscoroutinefunction(writer):
            self._writer = writer
            self._closer = None
        else:
            raise TypeError(
                f"WritableConnection or async function expected, got {type(writer)}"
            )

        self._encoder = encoder

    async def aclose(self) -> None:
        if self._closer:
            await self._closer()

    async def send(self, value: MessageType) -> None:
        await self._writer(self._encoder(value))


class MessageChannel(Channel[MessageType]):
    """Trio-style Channel_ that wraps a readable-writable connection and
    uses a parser to decode the messages read from the connection and an
    encoder to encode the messages to the wire format of the connection.
    """

    def __init__(
        self,
        connection: Connection,
        parser: Parser[RawType, MessageType],
        encoder: Encoder[MessageType, RawType],
    ):
        self._connection = connection
        self._encoder = encoder
        self._pending = deque()

        if callable(getattr(parser, "feed", None)):
            self._parser = parser.feed
        elif callable(parser):
            self._parser = parser
        else:
            raise TypeError(f"Parser or callable expected, got {type(parser)}")

    async def aclose(self) -> None:
        await self._connection.close()

    async def receive(self) -> MessageType:
        while not self._pending:
            await self._read()
        return self._pending.popleft()

    async def send(self, value: MessageType) -> None:
        await self._connection.write(self._encoder(value))

    async def _read(self) -> None:
        """Reads the pending bytes using the associated reader function and
        feeds the parsed messages into the pending list.

        Raises:
            EndOfChannel: if there is no more data to read from the connection
        """
        data = await self._connection.read()
        if not data:
            raise EndOfChannel()
        self._pending.extend(self._parser(data))
