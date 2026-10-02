from pytest import fixture, raises

from flockwave.server.show.formats import (
    SegmentEncoder,
    SkybrushBinaryFileFeatures,
    SkybrushBinaryFormatBlockType,
    SkybrushBinaryShowFile,
    TLVEncoder,
)
from flockwave.server.show.metadata import ShowMetadata
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


@fixture
def tlv_encoder() -> TLVEncoder:
    return TLVEncoder()


class TestTLVEncoder:
    def test_encode_entry(self, tlv_encoder: TLVEncoder):
        # GCS light control setup style: coordinates (tag 0, 4 bytes)
        assert tlv_encoder.encode_entry(0, b"\x01\x02\x03\x04") == (
            b"\x00\x04\x00\x01\x02\x03\x04"
        )

        # Empty value
        assert tlv_encoder.encode_entry(42, b"") == b"\x2a\x00\x00"

        # Show metadata style: drone index (tag 1, 2 bytes, little-endian)
        assert tlv_encoder.encode_entry(1, b"\x07\x00") == b"\x01\x02\x00\x07\x00"

    def test_encode_entry_maximum_length(self, tlv_encoder: TLVEncoder):
        value = b"\xff" * 65535
        result = tlv_encoder.encode_entry(0, value)
        assert result == b"\x00\xff\xff" + value

    def test_encode_entry_rejects_invalid_tag(self, tlv_encoder: TLVEncoder):
        with raises(ValueError, match="range 0-255"):
            tlv_encoder.encode_entry(256, b"")

        with raises(ValueError, match="range 0-255"):
            tlv_encoder.encode_entry(-1, b"")

    def test_encode_entry_rejects_too_long_value(self, tlv_encoder: TLVEncoder):
        with raises(ValueError, match="at most 65535"):
            tlv_encoder.encode_entry(0, b"x" * 65536)

    def test_encode_multiple_entries(self, tlv_encoder: TLVEncoder):
        entries = [(0, b"\x01\x02"), (2, b"\x0a\x00\x05\x00")]
        assert tlv_encoder.encode_multiple_entries(entries) == (
            b"\x00\x02\x00\x01\x02"  # first entry
            b"\x02\x04\x00\x0a\x00\x05\x00"  # second entry
        )

    def test_encode_multiple_entries_empty(self, tlv_encoder: TLVEncoder):
        assert tlv_encoder.encode_multiple_entries([]) == b""

    def test_iter_encode_multiple_entries(self, tlv_encoder: TLVEncoder):
        entries = [(0, b"\x01"), (1, b"\x02\x03")]
        result = list(tlv_encoder.iter_encode_multiple_entries(entries))

        assert result == [b"\x00\x01\x00\x01", b"\x01\x02\x00\x02\x03"]

        # Iterative encoding must yield the same as concatenated encoding
        assert b"".join(result) == tlv_encoder.encode_multiple_entries(entries)


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

    async def test_adding_metadata(self):
        uid = b"\xde\xad\xbe\xef"

        async with SkybrushBinaryShowFile.create_in_memory(version=1) as f:
            await f.add_metadata(ShowMetadata(uid=uid, drone_index=7))
            await f.finalize()

            expected_block = (
                # Block header: metadata block (type 8), 12 bytes of body
                b"\x08\x0c\x00"
                # UID entry: tag 0, 4-byte long value
                + b"\x00\x04\x00"
                + uid
                # Drone index entry: tag 1, 2-byte long little-endian value
                + b"\x01\x02\x00\x07\x00"
            )
            assert f.get_contents() == b"skyb\x01" + expected_block

    async def test_adding_metadata_with_default_values(self):
        async with SkybrushBinaryShowFile.create_in_memory(version=1) as f:
            await f.add_metadata(ShowMetadata())
            await f.finalize()

            assert f.get_contents() == (
                b"skyb\x01"
                # Block header: metadata block (type 8), 12 bytes of body
                + b"\x08\x0c\x00"
                # UID entry: tag 0, 4-byte long value, all zeros
                + b"\x00\x04\x00\x00\x00\x00\x00"
                # Drone index entry: tag 1, 2-byte long value, zero
                + b"\x01\x02\x00\x00\x00"
            )

    async def test_adding_metadata_then_reading_it_back(self):
        uid = b"\x01\x02\x03\x04"

        async with SkybrushBinaryShowFile.create_in_memory(version=1) as f:
            await f.add_metadata(ShowMetadata(uid=uid, drone_index=258))
            await f.finalize()
            data = f.get_contents()

        async with SkybrushBinaryShowFile.from_bytes(data) as f:
            blocks = await f.read_all_blocks()

            assert len(blocks) == 1
            assert blocks[0].type == SkybrushBinaryFormatBlockType.METADATA
            assert await blocks[0].read() == (
                b"\x00\x04\x00"
                + uid
                # Drone index 258 encoded as little-endian u16
                + b"\x01\x02\x00\x02\x01"
            )

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
