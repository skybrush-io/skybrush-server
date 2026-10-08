"""Tests for the show encoding process."""

from flockwave.server.ext.show.encoding import encode_show
from flockwave.server.show import (
    ShowEvent,
    ShowSpecification,
    SkybrushBinaryFormatBlockType,
)


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
        show[key] = value  # ty: ignore[invalid-key]

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
