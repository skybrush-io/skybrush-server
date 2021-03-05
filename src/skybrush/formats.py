"""Classes representing various Skybrush show file formats."""

from enum import IntEnum
from functools import partial
from io import BytesIO, SEEK_END
from math import floor
from struct import Struct
from trio import wrap_file
from typing import (
    AsyncIterable,
    Awaitable,
    Callable,
    IO,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

from .trajectory import TrajectorySegment, TrajectorySpecification
from .utils import Point

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

    async def add_light_program(self, data: bytes) -> None:
        """Adds a new light program block to the end of the Skybrush file
        with the given light program.

        Parameters:
            data: the light program, encoded in Skybrush format
        """
        return await self.add_block(SkybrushBinaryFormatBlockType.LIGHT_PROGRAM, data)

    async def add_trajectory(self, trajectory: TrajectorySpecification) -> None:
        """Adds a new trajectory block to the end of the Skybrush file
        with the given trajeectory.

        Parameters:
            trajectory: the trajectory to add
        """
        scaling_factor = trajectory.propose_scaling_factor()
        if scaling_factor >= 128:
            raise RuntimeError(
                "Trajectory covers too large an area for a Skybrush binary show file"
            )

        chunks = [bytes([scaling_factor])]  # MSB means whether to use yaw, but we won't
        encoder = SegmentEncoder(scaling_factor)

        # TODO(ntamas): replace this with SegmentEncoder.encode_multiple_segments()
        # once we have a test case in place for that
        first = True
        for segment in trajectory.segments():
            if first:
                # Encode the start point of the trajectory
                chunks.append(encoder.encode_point(segment.start))
                first = False

            # Encode the segment without its start point
            chunks.append(encoder.encode_segment(segment))

        return await self.add_block(
            SkybrushBinaryFormatBlockType.TRAJECTORY, b"".join(chunks)
        )

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


class SegmentEncoder:
    """Encoder class for trajectory segments in the Skybrush binary show file
    format.
    """

    _point_struct = Struct("<hhhh")
    _header_struct = Struct("<BH")

    def __init__(self, scale: float = 1):
        """Constructor.

        Parameters:
            scale: the scaling factor of the trajectory block; the real
                coordinates are multiplied by 1000 and then divided by this
                factor before rounding them to an integer that is then stored
                in the file. The scaling factor does not apply to the yaw;
                yaw angles are always encoded in 1/10th of degrees.
        """
        self._scale = 1000 / scale

    def encode_point(self, point: Point, yaw: float = 0.0) -> bytes:
        """Encodes the X, Y and Z coordinates of a point, followed by the given
        yaw coordinate.
        """
        x, y, z = self._scale_point(point)
        yaw = self._scale_yaw(yaw)
        return self._point_struct.pack(x, y, z, yaw)

    def encode_segment(self, segment: TrajectorySegment) -> bytes:
        """Encodes the control points and the end point of the given segment."""
        if not segment.has_control_points:
            # This is easier
            pass

        duration = floor(segment.duration * 1000)
        if duration < 0 or duration > 65535:
            raise RuntimeError(
                f"trajectory segment too long, got {duration} msec, max is 65535"
            )

        xs, ys, zs = zip(*(self._scale_point(point) for point in segment.points))
        x_format, xs = self._encode_coordinate_series(xs)
        y_format, ys = self._encode_coordinate_series(ys)
        z_format, zs = self._encode_coordinate_series(zs)

        header = self._header_struct.pack(
            x_format | (y_format << 2) | (z_format << 4), duration
        )

        parts = [header]
        parts.extend(xs)
        parts.extend(ys)
        parts.extend(zs)

        return b"".join(parts)

    def iter_encode_multiple_segments(
        self,
        segments: Iterable[TrajectorySegment],
        chunks: Optional[List[bytes]] = None,
    ) -> Iterable[bytes]:
        """Iteratively encodes a sequence of trajectory segments.

        Parameters:
            segments: the segments to encode

        Yields:
            the representation of the first point of the first segment, followed by
            the representation of each segment without its first point. (Note that
            the last point of each segment is the same as the first point of the
            next segment so the encoding does not lose information).
        """
        chunks = chunks or []

        first = True
        for segment in segments:
            if first:
                # Encode the start point of the trajectory
                yield self.encode_point(segment.start)
                first = False

            # Encode the segment without its start point
            yield self.encode_segment(segment)

        return chunks

    def _encode_coordinate_series(self, xs: Tuple[float]) -> Tuple[int, List[bytes]]:
        first, *xs = xs
        if all(x == first for x in xs):
            # segment is constant, this is easy
            return 0, b""

        if len(xs) == 2:
            # segment is a quadratic Bezier curve, we need to promote it to
            # cubic first
            xs = ((first + 2 * xs[0]) / 3, (2 * xs[0] + xs[1]) / 3, xs[1])

        coords = [x.to_bytes(2, byteorder="little", signed=True) for x in xs]
        if len(xs) == 1:
            # segment is linear
            return 1, coords

        if len(xs) == 3:
            # segment is a cubic Bezier curve
            return 2, coords

        if len(xs) == 7:
            # segment is a 7D polynomial curve
            return 2, coords

        # TODO(ntamas): convert 4-5-6D curves to 7D ones
        raise NotImplementedError(f"{len(xs)}D curves not implemented yet")

    def _scale_point(self, point: Point) -> Tuple[int, int, int]:
        return (
            round(point[0] * self._scale),
            round(point[1] * self._scale),
            round(point[2] * self._scale),
        )

    def _scale_yaw(self, yaw: float) -> int:
        yaw = round((yaw % 360) * 10)
        return yaw - 3600 if yaw >= 3600 else yaw
