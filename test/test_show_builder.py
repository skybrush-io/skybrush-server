"""Tests for the show file builder used during the show encoding process."""

from pytest import fixture, raises

from flockwave.server.show import (
    ShowEvent,
    ShowFileBuilder,
    SkybrushBinaryFormatBlockType,
    SkybrushBinaryShowFile,
)
from flockwave.server.show.metadata import ShowMetadata


@fixture
def show_file() -> SkybrushBinaryShowFile:
    return SkybrushBinaryShowFile.create_in_memory()


@fixture
def builder(show_file: SkybrushBinaryShowFile) -> ShowFileBuilder:
    return ShowFileBuilder(show_file)


class TestShowFileBuilder:
    async def test_create_in_memory(self):
        async with ShowFileBuilder.create_in_memory() as builder:
            builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)

            # The show file property is also usable while the builder is
            # being used; the builder is not closed yet. The pending events
            # are not encoded into the file until the builder is finalized,
            # so the file contains nothing but its v2 header at this point
            assert builder.show_file.get_contents() == b"skyb\x02\x01\x00\x00\x00\x00"

        # The finalized show file can be retrieved from the closed builder;
        # reading the blocks also validates the CRC of the file
        blocks = await builder.show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.EVENT_LIST
        assert await blocks[0].read() == ShowEvent(1.0, 10, 0, 0).encode()

    async def test_mark_handled(self, builder: ShowFileBuilder):
        assert builder.handled == frozenset()

        builder.mark_handled("pyro")
        builder.mark_handled("rthPlan")

        assert builder.handled == frozenset({"pyro", "rthPlan"})

    async def test_mark_handled_after_close_is_rejected(self, builder):
        async with builder:
            builder.mark_handled("pyro")

        # The set of handled keys remains readable after the builder is closed
        assert builder.handled == frozenset({"pyro"})

        with raises(RuntimeError, match="closed"):
            builder.mark_handled("rthPlan")

    async def test_properties(self, builder, show_file):
        assert builder.show_file is show_file

    async def test_add_block_with_custom_type(self, builder, show_file):
        await builder.add_block(100, b"hello")
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == 100
        assert await blocks[0].read() == b"hello"

    async def test_add_block_rejects_reserved_types(self, builder):
        with raises(ValueError, match="reserved"):
            await builder.add_block(SkybrushBinaryFormatBlockType.EVENT_LIST, b"")

    async def test_add_comment_is_delegated(self, builder, show_file):
        await builder.add_comment("hello world")
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.COMMENT
        assert await blocks[0].read() == b"hello world"

    async def test_events_are_sorted_and_encoded_at_finalize(self, builder, show_file):
        # Added in reverse chronological order to test sorting
        builder.add_event(timestamp=2.0, type=10, subtype=0, payload=3)
        builder.add_event(timestamp=1.0, type=10, subtype=0, payload=1)
        builder.add_event(timestamp=1.0, type=20, subtype=1, payload=2)
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.EVENT_LIST

        # Events with the same timestamp (1.0 seconds) keep their insertion
        # order; the event at 2.0 seconds comes last
        assert await blocks[0].read() == (
            ShowEvent(1.0, 10, 0, 1).encode()
            + ShowEvent(1.0, 20, 1, 2).encode()
            + ShowEvent(2.0, 10, 0, 3).encode()
        )

    async def test_no_event_list_block_when_no_events(self, builder, show_file):
        await builder.add_comment("nothing to see here")
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.COMMENT

    async def test_finalize_returns_finalized_show_file(self, builder, show_file):
        builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)
        finalized = await builder.finalize()
        assert finalized is show_file

        # Reading the blocks also validates the CRC of the file, ensuring
        # that the file was properly finalized
        blocks = await finalized.read_all_blocks()
        assert len(blocks) == 1

    async def test_finalize_is_allowed_only_once(self, builder):
        await builder.finalize()

        with raises(RuntimeError, match="closed"):
            await builder.finalize()

    async def test_modification_after_finalization_is_rejected(self, builder):
        builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)
        await builder.finalize()

        with raises(RuntimeError, match="closed"):
            builder.add_event(timestamp=2.0, type=10, subtype=0, payload=0)

        with raises(RuntimeError, match="closed"):
            await builder.add_block(100, b"hello")

        with raises(RuntimeError, match="closed"):
            await builder.add_comment("hello")

    async def test_context_manager_finalizes_on_success(self, builder, show_file):
        async with builder:
            builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)

        # Reading the blocks also validates the CRC of the file, ensuring
        # that the builder was finalized when the context was exited
        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.EVENT_LIST
        assert await blocks[0].read() == ShowEvent(1.0, 10, 0, 0).encode()

    async def test_context_manager_does_not_finalize_on_error(self, builder, show_file):
        with raises(ValueError, match="boom"):
            async with builder:
                builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)
                raise ValueError("boom")

        # The builder was closed without being finalized when the context
        # was exited with an exception, so the show file has no event list
        # block yet; skip the CRC validation as the file was not finalized
        blocks = await show_file.read_all_blocks(validate=False)
        assert not blocks

        # The builder is closed so it may neither be modified nor finalized
        with raises(RuntimeError, match="closed"):
            builder.add_event(timestamp=2.0, type=10, subtype=0, payload=0)

        with raises(RuntimeError, match="closed"):
            await builder.finalize()

    async def test_context_manager_does_not_finalize_twice(self, builder, show_file):
        async with builder:
            builder.add_event(timestamp=1.0, type=10, subtype=0, payload=0)
            await builder.finalize()

        # Explicit finalization inside the context manager prevents a second,
        # redundant finalization attempt when the context is exited
        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.EVENT_LIST

    async def test_add_encoded_gcs_light_control_setup(self, builder, show_file):
        data = b"\x01\x02\x03\x04\x05\x06"
        await builder.add_encoded_gcs_light_control_setup(data)
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.GCS_LIGHT_CONTROL_SETUP
        assert await blocks[0].read() == data

    async def test_add_encoded_rth_plan(self, builder, show_file):
        data = b"\x01\x02\x03\x04"
        await builder.add_encoded_rth_plan(data)
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.RTH_PLAN
        assert await blocks[0].read() == data

    async def test_add_encoded_yaw_setpoints(self, builder, show_file):
        data = b"\x01\x08\x02"
        await builder.add_encoded_yaw_setpoints(data)
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.YAW_CONTROL
        assert await blocks[0].read() == data

    async def test_add_tlv_block(self, builder, show_file):
        await builder.add_tlv_block(
            100,
            [
                (1, b"\xde\xad\xbe\xef"),
                (2, b"\x07\x00"),
            ],
        )
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == 100
        assert await blocks[0].read() == (
            # Entry 1: tag 1, 4-byte long value
            b"\x01\x04\x00\xde\xad\xbe\xef"
            # Entry 2: tag 2, 2-byte long value
            + b"\x02\x02\x00\x07\x00"
        )

    async def test_add_tlv_block_rejects_reserved_types(self, builder):
        with raises(ValueError, match="reserved"):
            await builder.add_tlv_block(
                SkybrushBinaryFormatBlockType.EVENT_LIST, [(0, b"\x00")]
            )

    async def test_add_metadata(self, builder, show_file):
        uid = b"\xde\xad\xbe\xef"

        await builder.add_metadata(ShowMetadata(uid=uid, drone_index=7))
        await builder.finalize()

        # Skip the header (10 bytes incl. the CRC, written by finalize())
        assert show_file.get_contents()[10:] == (
            # Block header: metadata block (type 8), 12 bytes of body
            b"\x08\x0c\x00"
            # UID entry: tag 0, 4-byte long value
            + b"\x00\x04\x00"
            + uid
            # Drone index entry: tag 1, 2-byte long little-endian value
            + b"\x01\x02\x00\x07\x00"
        )

    async def test_add_metadata_with_default_values(self, builder, show_file):
        await builder.add_metadata(ShowMetadata())
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.METADATA
        assert await blocks[0].read() == (
            # UID entry: tag 0, 4-byte long value, all zeros
            b"\x00\x04\x00\x00\x00\x00\x00"
            # Drone index entry: tag 1, 2-byte long value, zero
            + b"\x01\x02\x00\x00\x00"
        )

    async def test_add_metadata_then_reading_it_back(self, builder, show_file):
        uid = b"\x01\x02\x03\x04"

        await builder.add_metadata(ShowMetadata(uid=uid, drone_index=258))
        await builder.finalize()

        blocks = await show_file.read_all_blocks()
        assert len(blocks) == 1
        assert blocks[0].type == SkybrushBinaryFormatBlockType.METADATA
        assert await blocks[0].read() == (
            b"\x00\x04\x00"
            + uid
            # Drone index 258 encoded as little-endian u16
            + b"\x01\x02\x00\x02\x01"
        )
