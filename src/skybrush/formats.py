"""Classes representing various Skybrush show file formats."""

from enum import IntEnum
from functools import partial
from io import BytesIO, SEEK_END
from struct import Struct
from trio import wrap_file
from typing import AsyncIterable, Awaitable, Callable, IO, Optional, Union

__all__ = ("SkybrushBinaryShowFile",)


_SKYBRUSH_BINARY_FILE_HEADER = b"skyb"


async def _read_exactly(
    fp,
    length: int,
    offset: Optional[int] = None,
    *,
    message: str = "unexpected end of block in Skybrush file",
):
    if offset is not None:
        await fp.seek(offset)
    data = await fp.read(length)
    if len(data) != length:
        raise IOError(message)
    return data


class SkybrushBinaryFormatBlockType(IntEnum):
    """Enum representing the possible block types in a Skybrush binary file."""

    TRAJECTORY = 1
    LIGHT_PROGRAM = 2
    COMMENT = 3


class SkybrushBinaryFileBlock:
    """Class representing a single block in a Skybrush binary file."""

    def __init__(
        self,
        type: int,
        contents: Union[Optional[bytes], Callable[[], Awaitable[bytes]]],
    ):
        """Constructor.

        Parameters:
            type: type of the block
            contents: the contents of the block, or an async function that resolves
                to the contents of the block when invoked with no arguments
        """
        self.type = type

        if callable(contents):
            self._loader = contents
            self._contents = None
        else:
            self._loader = None
            self._contents = contents

    @property
    def consumed(self) -> bool:
        """Whether the block has already been consumed, i.e. loaded from the
        backing awaitable.

        Returns True if the block was constructed without an awaitable.
        """
        return self._loader is None

    async def read(self) -> bytes:
        """Reads the raw body of this block."""
        if self._contents is None and self._loader is not None:
            self._contents = await self._loader()
            self._loader = None
        return self._contents


class SkybrushBinaryShowFile:
    """Class representing a Skybrush binary show file, backed by a
    file-like object.
    """

    _header_struct = Struct("<BH")

    @classmethod
    def create_in_memory(cls, version: int = 1):
        return cls.from_bytes(data=None, version=version)

    @classmethod
    def from_bytes(cls, data: Optional[bytes] = None, *, version: int = 1):
        """Creates an in-memory Skybrush binary show file.

        Parameters:
            data: the show file data; `None` means to create a new show file
                with a header but no blocks yet
            version: the version number of the binary show file when it is
                created anew; ignored when `data` is not `None`
        """
        if not data:
            data = _SKYBRUSH_BINARY_FILE_HEADER + bytes([version])
        return cls(BytesIO(data))

    def __init__(self, fp: IO[bytes]):
        """Constructor.

        Parameters:
            fp: the file-like object that stores the show data
        """
        if isinstance(fp, BytesIO):
            self._buffer = fp

        self._fp = wrap_file(fp)
        self._version = None
        self._start_of_first_block = None

    async def __aenter__(self):
        await self._fp.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        return await self._fp.__aexit__(exc_type, exc_value, tb)

    async def _rewind(self) -> None:
        """Rewinds the internal read/write pointer of the underlying file-like
        object to the start of the first block in the file.
        """
        if self._start_of_first_block is None:
            await self._fp.seek(0)
            self._version = await self._expect_header()
            self._start_of_first_block = await self._fp.tell()

            if self._version != 1:
                raise RuntimeError("only version 1 files are supported")
        else:
            await self._fp.seek(self._start_of_first_block)

    async def _expect_header(self) -> int:
        """Reads the beginning of the buffer to check whether the Skybrush binary
        file header is to be found there. Throws a RuntimeError if the file
        header is invalid.

        Returns:
            the Skybrush binary file schema version
        """
        header = await self._fp.read(4)
        if header != _SKYBRUSH_BINARY_FILE_HEADER:
            raise RuntimeError("expected Skybrush binary file header, got {header!r}")

        version = await self._fp.read(1)
        return ord(version)

    async def add_block(self, type: SkybrushBinaryFormatBlockType, body: bytes) -> None:
        """Adds a new block to the end of the Skybrush file."""
        seekable = self._fp.seekable()

        if seekable:
            await self._fp.seek(0, SEEK_END)

        if len(body) >= 65536:
            raise ValueError(
                f"body too large; maximum allowed length is 65535 bytes, got {len(body)}"
            )

        header = self._header_struct.pack(type, len(body))
        await self._fp.write(header)
        await self._fp.write(body)

    async def add_comment(
        self, comment: Union[str, bytes], encoding: str = "utf-8"
    ) -> None:
        """Adds a new comment block to the end of the Skybrush file.

        Parameters:
            comment: the comment to add
            encoding: the encoding of the comment if it is a string; ignored when
                the comment is already a bytes object
        """
        if not isinstance(comment, bytes):
            comment = comment.encode(encoding)

        return await self.add_block(SkybrushBinaryFormatBlockType.COMMENT, comment)

    async def blocks(
        self, rewind: Optional[bool] = None
    ) -> AsyncIterable[SkybrushBinaryFileBlock]:
        """Iterates over the blocks found in the file.

        Parameters:
            rewind: whether to rewind the stream to the beginning before
                iterating. `None` means to rewind if and only if the stream is
                seekable.
        """
        seekable = self._fp.seekable()

        if rewind is None:
            rewind = seekable

        if rewind:
            await self._rewind()

        while True:
            data = await self._fp.read(self._header_struct.size)
            if not data:
                # End of stream
                break

            block_type, length = self._header_struct.unpack(data)

            if seekable:
                offset = await self._fp.tell()
                reader = partial(_read_exactly, self._fp, length, offset=offset)
            else:
                reader = partial(_read_exactly, self._fp, length)

            block = SkybrushBinaryFileBlock(block_type, reader)
            if seekable:
                end_of_block = await self._fp.tell()
                end_of_block += length

            yield block

            if seekable:
                await self._fp.seek(end_of_block)
            elif not block.consumed:
                await block.read()

    def get_buffer(self) -> IO[bytes]:
        """Returns the underlying buffer of the file if it is backed by an
        in-memory buffer.
        """
        if self._buffer:
            return self._buffer

        raise RuntimeError("file is not backed by an in-memory buffer")

    def get_contents(self) -> bytes:
        """Returns the contents of the underlying in-memory buffer of the file
        if it is backed by an in-memory buffer.
        """
        return self.get_buffer().getvalue()

    @property
    def version(self) -> int:
        """Returns the version number of the file."""
        return self._version
