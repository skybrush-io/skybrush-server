"""Tests for the show encoding process."""

from zlib import crc32

from pytest import raises

from flockwave.server.ext.show.encoding import encode_show
from flockwave.server.show import (
    ShowEvent,
    ShowSpecification,
    SkybrushBinaryFormatBlockType,
    get_show_metadata_from_show_specification,
)
from flockwave.server.show.metadata import NO_UID, ShowMetadata


def make_show(extra: dict[str, dict] | None = None) -> ShowSpecification:
    """Creates a minimal, valid show specification for testing purposes."""
    show: ShowSpecification = {
        "coordinateSystem": {
            "origin": [19.5, 47.0],
            "orientation": 0,
            "type": "nwu",
        },
        "trajectory": {"version": 1, "points": []},
    }

    for key, value in (extra or {}).items():
        show[key] = value

    return show


class TestEncodeShow:
    async def test_basic_show_is_encoded_without_warnings(self, caplog):
        with caplog.at_level("WARNING"):
            show_file = await encode_show(make_show())

        assert not [
            record for record in caplog.records if record.levelname == "WARNING"
        ]

        blocks = await show_file.read_all_blocks()
        assert [block.type for block in blocks] == [
            SkybrushBinaryFormatBlockType.TRAJECTORY,
            SkybrushBinaryFormatBlockType.LIGHT_PROGRAM,
        ]

    async def test_warning_for_unhandled_props(self, caplog):
        show = make_show({"pyro": {"version": 1}, "rthPlan": {"version": 1}})

        with caplog.at_level("WARNING"):
            await encode_show(show)

        warnings = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        assert len(warnings) == 1
        assert "pyro" in warnings[0]
        assert "rthPlan" in warnings[0]
        assert "yawControl" not in warnings[0]

    async def test_no_warning_when_hook_marks_props_as_handled(self, caplog):
        show = make_show({"pyro": {"version": 1}})

        async def hook(show, builder):
            builder.mark_handled("pyro")

        with caplog.at_level("WARNING"):
            await encode_show(show, hooks=[hook])

        assert not [
            record for record in caplog.records if record.levelname == "WARNING"
        ]

    async def test_hook_can_add_events_and_blocks(self, caplog):
        async def hook(show, builder):
            builder.add_event(timestamp=1.0, type=1, subtype=0, payload=0)
            await builder.add_comment("hello world")

        with caplog.at_level("WARNING"):
            show_file = await encode_show(make_show(), hooks=[hook])

        assert not [
            record for record in caplog.records if record.levelname == "WARNING"
        ]

        blocks = await show_file.read_all_blocks()
        assert [block.type for block in blocks] == [
            SkybrushBinaryFormatBlockType.TRAJECTORY,
            SkybrushBinaryFormatBlockType.LIGHT_PROGRAM,
            SkybrushBinaryFormatBlockType.COMMENT,
            SkybrushBinaryFormatBlockType.EVENT_LIST,
        ]
        assert await blocks[3].read() == ShowEvent(1.0, 1, 0, 0).encode()

    async def test_metadata_block_from_mission_info(self):
        show = make_show({"mission": {"id": "abc123", "index": 258, "numDrones": 300}})

        show_file = await encode_show(show)

        blocks = await show_file.read_all_blocks()
        assert [block.type for block in blocks] == [
            SkybrushBinaryFormatBlockType.TRAJECTORY,
            SkybrushBinaryFormatBlockType.LIGHT_PROGRAM,
            SkybrushBinaryFormatBlockType.METADATA,
        ]
        assert await blocks[2].read() == (
            b"\x00\x04\x00"
            + crc32(b"abc123").to_bytes(4, "big")
            + b"\x01\x02\x00\x02\x01"
        )


class TestGetShowMetadataFromShowSpecification:
    def test_no_mission_info(self):
        assert get_show_metadata_from_show_specification(make_show()) is None
        assert (
            get_show_metadata_from_show_specification(
                make_show({"mission": {"title": "foo", "numDrones": 5}})
            )
            is None
        )

    def test_mission_id_and_index(self):
        show = make_show({"mission": {"id": "abc123", "index": 7}})
        assert get_show_metadata_from_show_specification(show) == ShowMetadata(
            uid=crc32(b"abc123").to_bytes(4, "big"), drone_index=7
        )

    def test_mission_id_is_encoded_as_utf8(self):
        show = make_show({"mission": {"id": "árvíztűrő", "index": 0}})
        metadata = get_show_metadata_from_show_specification(show)
        assert metadata is not None
        assert metadata.uid == crc32("árvíztűrő".encode("utf-8")).to_bytes(4, "big")

    def test_empty_mission_id(self):
        show = make_show({"mission": {"id": "", "index": 3}})
        assert get_show_metadata_from_show_specification(show) == ShowMetadata(
            uid=NO_UID, drone_index=3
        )

    def test_missing_index(self):
        show = make_show({"mission": {"id": "abc123"}})
        metadata = get_show_metadata_from_show_specification(show)
        assert metadata is not None
        assert metadata.drone_index == 0

    def test_index_out_of_range(self):
        for index in (-1, 65536):
            show = make_show({"mission": {"id": "abc123", "index": index}})
            with raises(RuntimeError, match="outside valid range"):
                get_show_metadata_from_show_specification(show)
