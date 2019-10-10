"""Class that implements a Trio-style channel object that takes data from a
ReadableConnection_ and yields parsed message objects.
"""

from collections import deque
from inspect import iscoroutinefunction
from trio import EndOfChannel
from trio.abc import ReceiveChannel
from typing import Awaitable, Callable, Generic, TypeVar, Union

from .connections import ReadableConnection, WritableConnection
from .parsers import Parser

__all__ = ("ParserChannel",)

T = TypeVar("T")

Reader = Union[Callable[[], Awaitable[bytes]], ReadableConnection[bytes]]
Writer = Union[Callable[[bytes], None], WritableConnection[bytes]]


class ParserChannel(ReceiveChannel[T], Generic[T]):
    """Trio-style ReceiveChannel_ that takes data from a ReadableConnection_
    and yields parsed message objects.
    """

    def __init__(self, reader: Reader, parser: Parser[T]):
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

        self._feeder = parser.feed
        self._pending = deque()

    async def aclose(self) -> None:
        if self._closer:
            await self._closer()

    async def receive(self) -> T:
        while not self._pending:
            await self._read()

        return self._pending.popleft()

    async def _read(self) -> None:
        """Reads the pending bytes using the associated reader function and
        feeds the parsed messages into the pending list.
        """
        data = await self._reader()
        if not data:
            raise EndOfChannel()

        self._pending.extend(self._feeder(data))
