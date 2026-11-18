"""Tests for the selection of the positioning system in the `crazyflie`
extension.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from aiocflib.crazyflie.localization import LocalizationChannel
from pytest import mark, raises

from flockwave.server.ext.crazyflie.driver import CrazyflieDriver
from flockwave.server.ext.crazyflie.extension import CrazyflieDronesExtension
from flockwave.server.ext.crazyflie.lighthouse import LIGHTHOUSE_LPS_ID
from flockwave.server.ext.crazyflie.loco_lps import LOCO_LPS_ID
from flockwave.server.ext.crazyflie.mocap import CrazyflieMocapFrameHandler
from flockwave.server.ext.crazyflie.types import PositioningSystemType
from flockwave.server.ext.motion_capture import MotionCaptureFrame

from .crazyflie_extension import running_crazyflie_extension
from .recording_message_hub import RecordingMessageHub


def test_positioning_system_from_json():
    assert PositioningSystemType.from_json(None) is PositioningSystemType.UWB
    assert PositioningSystemType.from_json("uwb") is PositioningSystemType.UWB
    assert (
        PositioningSystemType.from_json("Lighthouse")
        is PositioningSystemType.LIGHTHOUSE
    )
    assert PositioningSystemType.from_json("mocap") is PositioningSystemType.MOCAP
    with raises(ValueError):
        PositioningSystemType.from_json("gps")


@mark.parametrize(
    "positioning,expected",
    [
        (None, PositioningSystemType.UWB),
        ("lighthouse", PositioningSystemType.LIGHTHOUSE),
        ("gps", PositioningSystemType.UWB),
    ],
)
def test_extension_configures_positioning_system(
    positioning: str | None, expected: PositioningSystemType
):
    ext = CrazyflieDronesExtension()
    ext.log = MagicMock()
    driver = CrazyflieDriver()
    cfg = {} if positioning is None else {"positioning": positioning}
    ext.configure_driver(driver, cfg)
    assert driver.positioning_system_type is expected
    assert ext.log.warning.called == (positioning == "gps")


@mark.parametrize(
    "positioning,expected_ids",
    [("uwb", [LOCO_LPS_ID]), ("mocap", []), ("lighthouse", [LIGHTHOUSE_LPS_ID])],
)
async def test_extension_registers_lps_of_selected_positioning_system(
    hub: RecordingMessageHub,
    tmp_path: Path,
    positioning: str,
    expected_ids: list[str],
):
    async with running_crazyflie_extension(hub, tmp_path, positioning) as (
        _,
        lps_ext,
        driver,
    ):
        assert list(lps_ext._lps_registry.ids) == expected_ids
        assert (driver.lighthouse is not None) == (positioning == "lighthouse")


def get_motion_capture_frame_handlers(ext: CrazyflieDronesExtension) -> list[Any]:
    assert ext.app is not None
    signals = ext.app.import_api("signals")
    return [
        call.args[0]["motion_capture:frame"]
        for call in signals.use.call_args_list
        if "motion_capture:frame" in call.args[0]
    ]


@mark.parametrize("positioning", ["uwb", "lighthouse"])
async def test_extension_ignores_motion_capture_frames_with_other_positioning(
    hub: RecordingMessageHub, tmp_path: Path, positioning: str
):
    async with running_crazyflie_extension(hub, tmp_path, positioning) as (
        ext,
        _,
        _,
    ):
        handlers = get_motion_capture_frame_handlers(ext)
        assert len(handlers) == 0


async def test_extension_does_not_ignore_motion_capture_frames_with_mocap(
    hub: RecordingMessageHub, tmp_path: Path
):
    async with running_crazyflie_extension(hub, tmp_path, "mocap") as (ext, _, _):
        # Frames are forwarded by the handlers of the radio connections; the
        # test runs without radio connections
        assert get_motion_capture_frame_handlers(ext) == []


def test_mocap_frame_handler_forwards_frames():
    broadcaster = MagicMock()
    handler = CrazyflieMocapFrameHandler(MagicMock(), broadcaster)
    frame = MotionCaptureFrame(timestamp=0.0, items=[])
    frame.add_item("7", (1.0, 2.0, 3.0))
    frame.add_item("8", (1.0, 2.0, 3.0), (1.0, 0.0, 0.0, 0.0))
    frame.add_item("not-a-number", (1.0, 2.0, 3.0))

    handler.on_frame_received(None, frame=frame)

    # One packet with the position of drone 7 and one with the pose of drone 8
    channels = [call.args[1] for call in broadcaster.call_args_list]
    assert channels == [
        LocalizationChannel.POSITION_PACKED,
        LocalizationChannel.GENERIC,
    ]
