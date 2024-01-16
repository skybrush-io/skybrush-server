import struct

from pytest import fixture, raises

from flockwave.server.show.formats import (
    RTHPlanEncoder,
    SegmentEncoder,
    SkybrushBinaryFileBlock,
    SkybrushBinaryFileFeatures,
    SkybrushBinaryShowFile,
    SkybrushBinaryFormatBlockType,
    YawSetpointEncoder,
)
from flockwave.server.show.rth_plan import RTHAction, RTHPlan, RTHPlanEntry
from flockwave.server.show.trajectory import TrajectorySegment
from flockwave.server.show.yaw import YawSetpointList


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


@fixture
def plan() -> RTHPlan:
    plan = RTHPlan()

    entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=15,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(30, 40),
        duration=50,
        post_delay=5,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=45,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(-40, -30),
        duration=50,
        pre_delay=2,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=65,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(30, 40),
        duration=30,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=80,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(30, 40),
        duration=30,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(time=105, action=RTHAction.LAND)
    plan.add_entry(entry)

    return plan


ENCODED_RTH_PLAN_WITH_PROPOSED_SCALING_FACTOR = (
    # Scaling factor
    b"\x02"
    # Number of points
    b"\x02\x00"
    # Point 1: (30, 40)
    b"\x98\x3a\x20\x4e"
    # Point 2: (-40, -30)
    b"\xe0\xb1\x68\xc5"
    # Number of entries
    b"\x06\x00"
    # Entry 1: time=0, land
    b"\x10\x00"
    # Entry 2: time since previous = 15, go to (30, 40) in 50s, post-delay=5
    b"\x21\x0f\x00\x32\x05"
    # Entry 3: time since previous = 30, go to (-40, -30) in 50s, pre-delay=2
    b"\x22\x1e\x01\x32\x02"
    # Entry 4: time since previous = 20, go to (30, 40) in 30s
    b"\x20\x14\x00\x1e"
    # Entry 5: time since previous = 15, otherwise same as previous
    b"\x00\x0f"
    # Entry 6: time since previous = 25, land
    b"\x10\x19"
)

ENCODED_RTH_PLAN_WITH_SCALING_FACTOR_10 = (
    # Scaling factor
    b"\x0a"
    # Number of points
    b"\x02\x00"
    # Point 1: (30, 40)
    b"\xb8\x0b\xa0\x0f"
    # Point 2: (-40, -30)
    b"\x60\xf0\x48\xf4"
    # Number of entries
    b"\x06\x00"
    # Entry 1: time=0, land
    b"\x10\x00"
    # Entry 2: time since previous = 15, go to (30, 40) in 50s, post-delay=5
    b"\x21\x0f\x00\x32\x05"
    # Entry 3: time since previous = 30, go to (-40, -30) in 50s, pre-delay=2
    b"\x22\x1e\x01\x32\x02"
    # Entry 4: time since previous = 20, go to (30, 40) in 30s
    b"\x20\x14\x00\x1e"
    # Entry 5: time since previous = 15, otherwise same as previous
    b"\x00\x0f"
    # Entry 6: time since previous = 25, land
    b"\x10\x19"
)


@fixture
def too_large_plan() -> RTHPlan:
    plan = RTHPlan()

    entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=15,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(30000, 40000),
        duration=50,
        post_delay=5,
    )
    plan.add_entry(entry)

    return plan


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

    async def test_adding_rth_plan_block(self, plan: RTHPlan):
        async with SkybrushBinaryShowFile.create_in_memory() as f:
            await f.add_rth_plan(plan)
            await f.finalize()
            contents = f.get_contents()

        blocks: list[SkybrushBinaryFileBlock] = []
        async with SkybrushBinaryShowFile.from_bytes(contents) as f:
            blocks = await f.read_all_blocks()

            assert len(blocks) == 1
            assert blocks[0].type == SkybrushBinaryFormatBlockType.RTH_PLAN
            assert (
                await blocks[0].read()
            ) == ENCODED_RTH_PLAN_WITH_PROPOSED_SCALING_FACTOR

    async def test_adding_rth_plan_block_too_large(self, too_large_plan: RTHPlan):
        async with SkybrushBinaryShowFile.create_in_memory() as f:
            with raises(RuntimeError):
                await f.add_rth_plan(too_large_plan)


class TestRTHPlanEncoder:
    async def test_encoding_basic_plan(self, plan: RTHPlan):
        encoder = RTHPlanEncoder(scale=10)
        data = encoder.encode(plan)
        assert data == ENCODED_RTH_PLAN_WITH_SCALING_FACTOR_10

    async def test_encoding_basic_plan_default_scaling_factor(self, plan: RTHPlan):
        encoder = RTHPlanEncoder(scale=plan.propose_scaling_factor())
        data = encoder.encode(plan)
        assert data == ENCODED_RTH_PLAN_WITH_PROPOSED_SCALING_FACTOR

    async def test_encoding_basic_plan_with_invalid_scale(self, plan: RTHPlan):
        encoder = RTHPlanEncoder(scale=1)
        with raises(struct.error):
            encoder.encode(plan)

    async def test_encoding_plan_with_negative_step_duration(self):
        plan = RTHPlan()
        entry = RTHPlanEntry(
            time=15,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(30, 40),
            duration=-50,
            post_delay=5,
        )
        plan.add_entry(entry)
        with raises(ValueError, match="negative duration: -50"):
            RTHPlanEncoder(scale=plan.propose_scaling_factor()).encode(plan)

    async def test_encoding_plan_with_unknown_action(self):
        plan = RTHPlan()
        entry = RTHPlanEntry(
            time=15,
            action="no-such-action",  # type: ignore
            target=(30, 40),
            duration=-50,
            post_delay=5,
        )
        plan.add_entry(entry)
        with raises(ValueError, match="unknown RTH action: no-such-action"):
            RTHPlanEncoder(scale=100).encode(plan)


@fixture
def setpoints() -> YawSetpointList:
    return YawSetpointList(setpoints=[(10, 30), (20, 90), (25, -90)])


ENCODED_YAW_SETPOINTS = (
    # Yaw control block header
    b"\x00\x2c\x01"  # auto_yaw=0, yaw_offset=300
    # Relative yaw setpoints
    b"\x10\x27\x00\x00"  # dt=10000, dy=0
    b"\x10\x27\x58\x02"  # dt=10000, dy=600
    b"\x88\x13\xf8\xf8"  # dt=5000, dy=-1800
)


class TestYawSetpointEncoder:
    async def test_encoding_basic(self, setpoints: YawSetpointList):
        encoder = YawSetpointEncoder()
        data = encoder.encode(setpoints)
        assert data == ENCODED_YAW_SETPOINTS
