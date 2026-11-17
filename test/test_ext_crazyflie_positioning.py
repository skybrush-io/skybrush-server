"""Tests for the selection of the positioning system in the `crazyflie`
extension.
"""

from pathlib import Path
from unittest.mock import MagicMock

from pytest import mark, raises

from flockwave.server.ext.crazyflie.driver import CrazyflieDriver
from flockwave.server.ext.crazyflie.extension import CrazyflieDronesExtension
from flockwave.server.ext.crazyflie.lighthouse import LIGHTHOUSE_LPS_ID
from flockwave.server.ext.crazyflie.loco_lps import LOCO_LPS_ID
from flockwave.server.ext.crazyflie.types import PositioningSystemType

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
