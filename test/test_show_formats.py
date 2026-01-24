from pytest import raises

from flockwave.server.show.formats import (
    SegmentEncoder,
    SkybrushBinaryFileFeatures,
    SkybrushBinaryFormatBlockType,
    SkybrushBinaryShowFile,
)
from flockwave.server.show.trajectory import TrajectorySegment

SIMPLE_SKYB_FILE_V1 = (
    # Header, version 1
    b"skyb\x01"
    # Trajectory block starts here
    b"\x01$\x00\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
    b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
    b"\x10\x10'\x00\x00"
    # Comment block starts here
    b"\x03\x13\x00this is a test file"
)


SIMPLE_SKYB_FILE_V2 = (
    # Header, version 2, feature flags
    b"skyb\x02\x01"
    # Checksum
    b"\x93\x96\xe5\xdd"
    # Trajectory block starts here
    b"\x01$\x00\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
    b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
    b"\x10\x10'\x00\x00"
    # Comment block starts here
    b"\x03\x13\x00this is a test file"
    # Yaw control block starts here
    b"\x05\x03\x00\x01\x08\x02"
)


class TestSegmentEncoder:
    def test_encode_point(self):
        encoder = SegmentEncoder()
        assert (
            encoder.encode_point((10, 20, 30), yaw=45)
            == b"\x10\x27\x20\x4e\x30\x75\xc2\x01"
        )

        encoder = SegmentEncoder(scale=5)
        assert (
            encoder.encode_point((10, 20, 30), yaw=45)
            == b"\xd0\x07\xa0\x0f\x70\x17\xc2\x01"
        )

    def test_encode_segment(self):
        encoder = SegmentEncoder(scale=5)

        # Constant segment, start point not encoded
        segment = TrajectorySegment(t=15, duration=20, points=[(10, 20, 30)])
        assert encoder.encode_segment(segment) == b"\x00\x20\x4e"

        # Constant segment disguised as a linear one, start point and XY coords not encoded
        segment = TrajectorySegment(
            t=15, duration=20, points=[(10, 20, 30), (10, 20, 30)]
        )
        assert encoder.encode_segment(segment) == b"\x00\x20\x4e"

        # Linear segment along Z only, start point and XY coords not encoded
        segment = TrajectorySegment(
            t=15, duration=20, points=[(10, 20, 20), (10, 20, 30)]
        )
        assert encoder.encode_segment(segment) == b"\x10\x20\x4e\x70\x17"

        # Linear segment along XYZ, start point not encoded
        segment = TrajectorySegment(
            t=15, duration=20, points=[(5, 10, 20), (10, 20, 30)]
        )
        assert (
            encoder.encode_segment(segment) == b"\x15\x20\x4e\xd0\x07\xa0\x0f\x70\x17"
        )

        # Cubic Bezier segment along XYZ, start point not encoded
        segment = TrajectorySegment(
            t=15,
            duration=15,
            points=[(5, 10, 20), (5, 10, 20), (10, 20, 30), (10, 20, 30)],
        )
        assert (
            encoder.encode_segment(segment)
            == b"\x2a\x98\x3a\xe8\x03\xd0\x07\xd0\x07\xd0\x07\xa0\x0f\xa0\x0f\xa0\x0f\x70\x17\x70\x17"
        )

    def test_encode_long_segment_error(self):
        encoder = SegmentEncoder(scale=5)

        # Too long segment
        segment = TrajectorySegment(
            t=15, duration=66, points=[(5, 10, 20), (10, 20, 30)]
        )
        with raises(RuntimeError, match="trajectory segment must be"):
            encoder.encode_segment(segment)

    def test_encode_multiple_segments(self):
        encoder = SegmentEncoder()
        segments = [
            TrajectorySegment(
                t=0,
                duration=5,
                points=[(10, 20, 0), (10, 20, 0), (10, 20, 20), (10, 20, 20)],
            ),
            TrajectorySegment(
                t=5,
                duration=10,
                points=[(10, 20, 20), (10, 20, 20), (20, 20, 20), (20, 20, 20)],
            ),
            TrajectorySegment(
                t=15,
                duration=10,
                points=[(20, 20, 20), (20, 20, 20), (20, 10, 20), (20, 10, 20)],
            ),
            TrajectorySegment(
                t=25,
                duration=5,
                points=[(20, 10, 20), (20, 10, 20), (20, 10, 0), (20, 10, 0)],
            ),
        ]

        assert encoder.encode_multiple_segments([]) == b""
        assert encoder.encode_multiple_segments(segments[:1]) == (
            # Start point: (10, 20, 0), yaw = 0
            b"\x10' N\x00\x00\x00\x00"
            # First segment: cubic Bezier, changing in Z only
            b" \x88\x13\x00\x00 N N"
        )
        assert encoder.encode_multiple_segments(segments) == (
            # Start point: (10, 20, 0), yaw = 0
            b"\x10' N\x00\x00\x00\x00"
            # First segment: cubic Bezier, changing in Z only
            b" \x88\x13\x00\x00 N N"
            # Second segment: cubic Bezier, changing in X only
            b"\x02\x10'\x10' N N"
            # Third segment: cubic Bezier, changing in Y only
            b"\x08\x10' N\x10'\x10'"
            # Fourth segment: cubic Bezier, changing in Z only
            b" \x88\x13 N\x00\x00\x00\x00"
        )


class TestSkybrushBinaryFileFormat:
    async def test_reading_blocks_version_1(self):
        async with SkybrushBinaryShowFile.from_bytes(SIMPLE_SKYB_FILE_V1) as f:
            blocks = await f.read_all_blocks()

            assert f.version == 1
            assert not f.features
            assert len(blocks) == 2

            assert blocks[0].type == SkybrushBinaryFormatBlockType.TRAJECTORY
            data = await blocks[0].read()
            assert data == (
                b"\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
                b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
                b"\x10\x10'\x00\x00"
            )

            assert blocks[1].type == SkybrushBinaryFormatBlockType.COMMENT
            data = await blocks[1].read()
            assert data == (b"this is a test file")

    async def test_reading_blocks_version_2(self):
        async with SkybrushBinaryShowFile.from_bytes(SIMPLE_SKYB_FILE_V2) as f:
            blocks = await f.read_all_blocks()

            assert f.version == 2
            assert f.features == SkybrushBinaryFileFeatures.CRC32
            assert len(blocks) == 3

            assert blocks[0].type == SkybrushBinaryFormatBlockType.TRAJECTORY
            data = await blocks[0].read()
            assert data == (
                b"\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
                b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
                b"\x10\x10'\x00\x00"
            )

            assert blocks[1].type == SkybrushBinaryFormatBlockType.COMMENT
            data = await blocks[1].read()
            assert data == (b"this is a test file")

            assert blocks[2].type == SkybrushBinaryFormatBlockType.YAW_CONTROL
            data = await blocks[2].read()
            assert data == (b"\x01\x08\x02")

    async def test_reading_blocks_version_2_invalid_crc(self):
        data = SIMPLE_SKYB_FILE_V2[:6] + b"\x00" + SIMPLE_SKYB_FILE_V2[7:]
        with raises(RuntimeError, match="CRC error"):
            async with SkybrushBinaryShowFile.from_bytes(data) as f:
                await f.read_all_blocks()

    async def test_adding_blocks_version_1(self):
        async with SkybrushBinaryShowFile.create_in_memory(version=1) as f:
            await f.add_block(
                SkybrushBinaryFormatBlockType.TRAJECTORY,
                b"\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
                b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
                b"\x10\x10'\x00\x00",
            )
            await f.add_comment("this is a test file")
            await f.finalize()
            assert f.get_contents() == SIMPLE_SKYB_FILE_V1

    async def test_adding_blocks_version_2_with_checksum(self):
        async with SkybrushBinaryShowFile.create_in_memory(version=2) as f:
            await f.add_block(
                SkybrushBinaryFormatBlockType.TRAJECTORY,
                b"\n\x00\x00\x00\x00\x00\x00\x00\x00\x10\x10'\xe8\x03"
                b"\x01\x10'\xe8\x03\x04\x10'\xe8\x03\x05\x10'\x00\x00\x00\x00"
                b"\x10\x10'\x00\x00",
            )
            await f.add_comment("this is a test file")
            await f.add_block(
                SkybrushBinaryFormatBlockType.YAW_CONTROL,
                b"\x01\x08\x02",
            )
            await f.finalize()
            assert f.get_contents() == SIMPLE_SKYB_FILE_V2

    async def test_adding_block_that_is_too_large(self):
        async with SkybrushBinaryShowFile.create_in_memory() as f:
            with raises(ValueError, match="body too large"):
                await f.add_block(
                    SkybrushBinaryFormatBlockType.TRAJECTORY,
                    b"\x00" * 128 * 1024,
                )

    async def test_invalid_magic_marker(self):
        with raises(RuntimeError, match="expected Skybrush binary file header"):
            async with SkybrushBinaryShowFile.from_bytes(b"not-a-skyb-file") as f:
                await f.read_all_blocks()

    async def test_invalid_version(self):
        with raises(RuntimeError, match="version"):
            async with SkybrushBinaryShowFile.from_bytes(b"skyb\xff") as f:
                await f.read_all_blocks()
