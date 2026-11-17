"""Tests for the Lighthouse positioning system support of the `crazyflie`
extension.
"""

import json
from math import cos, isclose, pi, sin
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from aiocflib.crazyflie.lighthouse import (
    LighthouseBsCalibration,
    LighthouseBsGeometry,
    LighthouseCalibrationSweep,
    LighthouseConfiguration,
    LighthouseSystemType,
)
from flockwave.spec.errors import FlockwaveErrorCode
from pytest import mark, raises
from trio import open_nursery, sleep
from trio.testing import wait_all_tasks_blocked

from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.crazyflie.extension import CrazyflieDronesExtension
from flockwave.server.ext.crazyflie.lighthouse import (
    RETRY_INTERVAL,
    LighthouseLocalPositioningSystem,
    LighthouseLocalPositioningSystemType,
    are_lighthouse_configurations_equal,
    lighthouse_configuration_from_json,
    lighthouse_configuration_to_json,
    read_lighthouse_configuration,
    write_lighthouse_configuration,
)
from flockwave.server.ext.crazyflie.math import (
    Matrix3x3,
    quaternion_from_rotation_matrix,
    rotation_matrix_from_quaternion,
)
from flockwave.server.model import Quaternion
from flockwave.server.model.commands import Progress

from .crazyflie_extension import running_crazyflie_extension
from .recording_message_hub import RecordingMessageHub


def assert_quaternions_close(actual: Quaternion, expected: Quaternion) -> None:
    assert all(isclose(a, e, abs_tol=1e-6) for a, e in zip(actual, expected)), (
        actual,
        expected,
    )


IDENTITY3x3: Matrix3x3 = ((1, 0, 0), (0, 1, 0), (0, 0, 1))

# Base station facing the -X direction, tilted downwards by 30 degrees
FACING_BACKWARDS = rotation_matrix_from_quaternion((0, -sin(pi / 12), 0, cos(pi / 12)))


def roundtrip(config: LighthouseConfiguration) -> LighthouseConfiguration:
    """Simulates writing a configuration to a Crazyflie and reading it back,
    which loses precision.
    """
    return LighthouseConfiguration(
        system_type=config.system_type,
        geometries={
            index: LighthouseBsGeometry.from_bytes(geometry.to_bytes())
            for index, geometry in config.geometries.items()
        },
        calibrations={
            index: LighthouseBsCalibration.from_bytes(calibration.to_bytes())
            for index, calibration in config.calibrations.items()
        },
    )


def create_configuration(
    *indices: int, system_type: LighthouseSystemType = LighthouseSystemType.V2
) -> LighthouseConfiguration:
    sweep = LighthouseCalibrationSweep(phase=0.1, tilt=-0.05, curve=0.2)
    return LighthouseConfiguration(
        system_type=system_type,
        geometries={
            index: LighthouseBsGeometry(
                origin=(index + 0.1, -2.2, 2.3),
                rotation_matrix=FACING_BACKWARDS,
                valid=True,
            )
            for index in indices
        },
        calibrations={
            index: LighthouseBsCalibration(
                sweeps=(sweep, sweep), uid=0x1000 + index, valid=True
            )
            for index in indices
        },
    )


class FakeUAV:
    """Fake Crazyflie UAV that stores its Lighthouse configuration in memory."""

    def __init__(
        self,
        id: str,
        config: LighthouseConfiguration | None = None,
        *,
        supported: bool = True,
    ):
        self.id = id
        self.config = roundtrip(config or LighthouseConfiguration())
        self.error: Exception | None = None
        self.is_idle_on_ground = True
        self.reads = 0
        self.supported = supported
        self.writes = 0

    async def get_lighthouse_configuration(self) -> LighthouseConfiguration:
        await sleep(0.1)
        if not self.supported:
            raise NotSupportedError("No Lighthouse support")
        self.reads += 1
        return self.config

    async def set_lighthouse_configuration(
        self, config: LighthouseConfiguration
    ) -> None:
        await sleep(0.1)
        if self.error:
            raise self.error
        self.writes += 1
        self.config = roundtrip(config)


def create_lps(*uavs: FakeUAV) -> LighthouseLocalPositioningSystem:
    uavs_by_id = {uav.id: uav for uav in uavs}
    lps_type = LighthouseLocalPositioningSystemType(uavs_by_id.get, MagicMock())
    lps = lps_type.create()
    lps._id = "lighthouse"
    lps._schema_validator_getter = lps_type.get_configuration_schema_validator
    return lps


CFCLIENT_CONFIGURATION: dict[str, Any] = {
    "version": "1",
    "type": "lighthouse_system_configuration",
    "systemType": 2,
    "geos": {
        "0": {
            "origin": [-2.0, 1.5, 2.5],
            "rotation": [list(row) for row in IDENTITY3x3],
        },
        "3": {
            "origin": [2.0, -1.5, 2.5],
            "rotation": [list(row) for row in FACING_BACKWARDS],
        },
    },
    "calibs": {
        "0": {
            "sweeps": [
                {
                    "phase": 0.0,
                    "tilt": -0.047,
                    "curve": 0.5,
                    "gibmag": 0.0,
                    "gibphase": 0.0,
                    "ogeemag": 0.0,
                    "ogeephase": 0.0,
                },
                {
                    "phase": 0.0,
                    "tilt": 0.048,
                    "curve": 0.4,
                    "gibmag": 0.0,
                    "gibphase": 0.0,
                    "ogeemag": 0.0,
                    "ogeephase": 0.0,
                },
            ],
            "uid": 2899536323,
        },
        "5": {
            "sweeps": [
                {
                    "phase": 0.0,
                    "tilt": -0.047,
                    "curve": 0.5,
                    "gibmag": 0.0,
                    "gibphase": 0.0,
                    "ogeemag": 0.0,
                    "ogeephase": 0.0,
                },
            ]
            * 2,
            "uid": 1,
        },
    },
}


###########################################################################
# Rotation matrix conversion


@mark.parametrize(
    "q",
    [
        (1, 0, 0, 0),
        (cos(pi / 4), 0, 0, sin(pi / 4)),
        (0, 1, 0, 0),
        (0, 0, 1, 0),
        (0, 0, 0, 1),
        (0, -sin(pi / 12), 0, cos(pi / 12)),
        (0.5, -0.5, 0.5, 0.5),
        (0.1, 0.7, -0.5, 0.5),
    ],
)
def test_quaternion_from_rotation_matrix(q: Quaternion):
    assert_quaternions_close(
        quaternion_from_rotation_matrix(rotation_matrix_from_quaternion(q)), q
    )


def test_quaternion_from_rotation_matrix_normalizes_imprecise_matrices():
    geometry = LighthouseBsGeometry(rotation_matrix=FACING_BACKWARDS, valid=True)
    matrix = LighthouseBsGeometry.from_bytes(geometry.to_bytes()).rotation_matrix
    q = quaternion_from_rotation_matrix(matrix)
    assert isclose(sum(x * x for x in q), 1.0)
    assert_quaternions_close(q, (0, -sin(pi / 12), 0, cos(pi / 12)))


@mark.parametrize(
    "matrix",
    [
        ((0, 0, 0), (0, 0, 0), (0, 0, 0)),
        ((-1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ],
)
def test_quaternion_from_rotation_matrix_rejects_non_rotations(matrix: Matrix3x3):
    with raises(ValueError):
        quaternion_from_rotation_matrix(matrix)


###########################################################################
# Configuration format


def test_configuration_json_roundtrip():
    config = lighthouse_configuration_from_json(CFCLIENT_CONFIGURATION)  # type: ignore
    assert config.system_type is LighthouseSystemType.V2
    assert sorted(config.geometries) == [0, 3]
    assert sorted(config.calibrations) == [0, 5]
    assert config.geometries[3].origin == (2.0, -1.5, 2.5)
    assert config.calibrations[0].uid == 2899536323

    assert lighthouse_configuration_to_json(config) == CFCLIENT_CONFIGURATION


def test_configuration_json_omits_unknown_system_type():
    config = create_configuration(1, system_type=LighthouseSystemType.UNKNOWN)
    assert "systemType" not in lighthouse_configuration_to_json(config)


def test_configuration_schema_accepts_cfclient_files():
    validator = LighthouseLocalPositioningSystemType(
        lambda _: None
    ).get_configuration_schema_validator()
    validator(CFCLIENT_CONFIGURATION)
    validator({})


@mark.parametrize(
    "cfg",
    [
        {"geos": {"16": CFCLIENT_CONFIGURATION["geos"]["0"]}},
        {"geos": {"a": CFCLIENT_CONFIGURATION["geos"]["0"]}},
        {"geos": {"0": {"origin": [0, 0, 0]}}},
        {"geos": {"0": {"origin": [0, 0], "rotation": IDENTITY3x3}}},
        {"calibs": {"0": {"sweeps": [], "uid": 0}}},
        {"systemType": 3},
        {"foo": "bar"},
    ],
)
async def test_lighthouse_lps_rejects_invalid_configuration(cfg: dict[str, Any]):
    lps = create_lps()
    with raises(RuntimeError, match="Invalid configuration"):
        await lps.configure(cfg)


def test_configurations_are_compared_in_single_precision():
    config = create_configuration(0, 1)
    assert config != roundtrip(config)
    assert are_lighthouse_configurations_equal(config, roundtrip(config))

    assert not are_lighthouse_configurations_equal(
        config, create_configuration(0, 1, 2)
    )
    assert not are_lighthouse_configurations_equal(
        config, create_configuration(0, 1, system_type=LighthouseSystemType.V1)
    )
    assert are_lighthouse_configurations_equal(
        config, create_configuration(0, 1, system_type=LighthouseSystemType.UNKNOWN)
    )


###########################################################################
# Local positioning system


async def test_lighthouse_lps_has_no_configuration_initially():
    lps = create_lps()
    assert lps.configuration is None
    assert lps.anchors == []
    assert await lps.get_configuration() == {
        "version": "1",
        "type": "lighthouse_system_configuration",
        "geos": {},
        "calibs": {},
    }
    assert list(lps.features) == ["calibrate", "configure"]


async def test_lighthouse_lps_configuration_creates_anchors():
    lps = create_lps()
    updates = []

    with lps.on_updated.connected_to(updates.append, sender=lps):
        await lps.configure(CFCLIENT_CONFIGURATION)

    assert updates == [lps]
    assert await lps.get_configuration() == CFCLIENT_CONFIGURATION
    assert [anchor.id for anchor in lps.anchors] == ["1", "4", "6"]

    first, second, third = lps.anchors
    assert first.active and second.active
    assert first.position is not None
    assert (first.position.x, first.position.y, first.position.z) == (-2, 1.5, 2.5)
    assert first.position.json == [-2000, 1500, 2500]
    assert first.attitude == (1, 0, 0, 0)
    assert second.attitude is not None
    assert_quaternions_close(second.attitude, (0, -sin(pi / 12), 0, cos(pi / 12)))
    assert not first.errors and not second.errors

    # Base station 6 has calibration data but no geometry
    assert not third.active
    assert third.position is None and third.attitude is None
    assert list(third.errors) == [FlockwaveErrorCode.CONFIGURATION_ERROR]


async def test_lighthouse_lps_flags_invalid_rotation_matrices():
    lps = create_lps()
    await lps.configure(
        {"geos": {"0": {"origin": [0, 0, 0], "rotation": [[0, 0, 0]] * 3}}}
    )
    (anchor,) = lps.anchors
    assert anchor.position is not None and anchor.attitude is None
    assert list(anchor.errors) == [FlockwaveErrorCode.CONFIGURATION_ERROR]


async def test_lighthouse_lps_empty_configuration_clears_configuration():
    lps = create_lps()
    await lps.configure(CFCLIENT_CONFIGURATION)
    await lps.configure({"geos": {}, "calibs": {}})
    assert lps.configuration is None
    assert lps.anchors == []


###########################################################################
# Synchronization with drones


async def test_lighthouse_lps_does_not_touch_drones_without_configuration(
    autojump_clock,
):
    uav = FakeUAV("01", create_configuration(0))
    lps = create_lps(uav)

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(10)
        nursery.cancel_scope.cancel()

    assert uav.reads == 0 and uav.writes == 0


async def test_lighthouse_lps_writes_configuration_to_drones(autojump_clock):
    config = create_configuration(0, 1)
    up_to_date = FakeUAV("01", config)
    outdated = FakeUAV("02", create_configuration(0))
    lps = create_lps(up_to_date, outdated)

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, up_to_date)
        nursery.start_soon(lps.keep_uav_in_sync, outdated)
        await wait_all_tasks_blocked()

        lps._set_configuration(config)
        await sleep(10)
        assert up_to_date.writes == 0
        assert outdated.writes == 1
        assert are_lighthouse_configurations_equal(outdated.config, config)

        # Changing the configuration again updates both drones
        new_config = create_configuration(0, 1, 2)
        lps._set_configuration(new_config)
        await sleep(10)
        assert up_to_date.writes == 1
        assert outdated.writes == 2
        assert are_lighthouse_configurations_equal(up_to_date.config, new_config)

        nursery.cancel_scope.cancel()


async def test_lighthouse_lps_writes_configuration_to_new_drones(autojump_clock):
    config = create_configuration(0, 1)
    lps = create_lps()
    lps._set_configuration(config)
    uav = FakeUAV("01")

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(10)
        assert uav.reads == 1 and uav.writes == 1
        assert are_lighthouse_configurations_equal(uav.config, config)
        nursery.cancel_scope.cancel()


async def test_lighthouse_lps_waits_until_drone_is_idle(autojump_clock):
    uav = FakeUAV("01")
    uav.is_idle_on_ground = False
    lps = create_lps(uav)
    lps._set_configuration(create_configuration(0))

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(60)
        assert uav.reads == 0 and uav.writes == 0

        # Changes made while waiting are taken into account
        config = create_configuration(0, 1)
        lps._set_configuration(config)
        uav.is_idle_on_ground = True
        await sleep(10)
        assert uav.writes == 1
        assert are_lighthouse_configurations_equal(uav.config, config)
        nursery.cancel_scope.cancel()


async def test_lighthouse_lps_skips_drones_without_lighthouse_support(
    autojump_clock,
):
    uav = FakeUAV("01", supported=False)
    lps = create_lps(uav)
    lps._set_configuration(create_configuration(0))

    # Task must return on its own
    await lps.keep_uav_in_sync(uav)
    assert uav.writes == 0


async def test_lighthouse_lps_logs_failed_writes(autojump_clock):
    uav = FakeUAV("01")
    uav.error = OSError("Communication error")
    lps = create_lps(uav)
    assert isinstance(lps.log, MagicMock)
    lps._set_configuration(create_configuration(0))

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(10)
        lps.log.warning.assert_called_once()
        assert "Communication error" in lps.log.warning.call_args.args[0]
        assert lps.log.warning.call_args.kwargs["extra"] == {"id": "01"}

        # Task keeps on running and retries when the configuration changes
        uav.error = None
        lps._set_configuration(create_configuration(0, 1))
        await sleep(10)
        assert uav.writes == 1
        nursery.cancel_scope.cancel()


async def run_sync_task_for(lps: LighthouseLocalPositioningSystem, uav: FakeUAV):
    """Simulates a connection to the given UAV that lasts for ten seconds."""
    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(10)
        nursery.cancel_scope.cancel()


async def test_lighthouse_lps_checks_drones_only_once(autojump_clock):
    config = create_configuration(0, 1)
    up_to_date = FakeUAV("01", config)
    outdated = FakeUAV("02")
    lps = create_lps(up_to_date, outdated)
    lps._set_configuration(config)

    await run_sync_task_for(lps, up_to_date)
    await run_sync_task_for(lps, outdated)
    assert (up_to_date.reads, up_to_date.writes) == (1, 0)
    assert (outdated.reads, outdated.writes) == (1, 1)

    # Reconnections do not trigger another check
    await run_sync_task_for(lps, up_to_date)
    await run_sync_task_for(lps, outdated)
    assert (up_to_date.reads, up_to_date.writes) == (1, 0)
    assert (outdated.reads, outdated.writes) == (1, 1)


async def test_lighthouse_lps_checks_drones_again_when_configuration_is_set(
    autojump_clock,
):
    config = create_configuration(0, 1)
    uav = FakeUAV("01")
    lps = create_lps(uav)
    lps._set_configuration(config)
    await run_sync_task_for(lps, uav)
    assert (uav.reads, uav.writes) == (1, 1)

    # E.g., the drone was replaced with a spare one that has the same address
    uav.config = LighthouseConfiguration()

    # Setting the same configuration again checks all drones again
    await lps.configure(lighthouse_configuration_to_json(config))
    await run_sync_task_for(lps, uav)
    assert (uav.reads, uav.writes) == (2, 2)
    assert are_lighthouse_configurations_equal(uav.config, config)


async def test_lighthouse_lps_does_not_check_import_source_again(autojump_clock):
    source = FakeUAV("01", create_configuration(0, 1))
    lps = create_lps(source)

    await run_calibration(lps, {"method": "import", "uav": "01"})
    assert source.reads == 1

    await run_sync_task_for(lps, source)
    assert (source.reads, source.writes) == (1, 0)


async def test_lighthouse_lps_retries_failed_writes_periodically(autojump_clock):
    uav = FakeUAV("01")
    uav.error = OSError("Communication error")
    lps = create_lps(uav)
    assert isinstance(lps.log, MagicMock)
    lps._set_configuration(create_configuration(0))

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)
        await sleep(RETRY_INTERVAL * 2.5)

        # Only the first failure is logged as a warning
        assert uav.reads == 3
        assert lps.log.warning.call_count == 1
        assert lps.log.debug.call_count == 2

        uav.error = None
        await sleep(RETRY_INTERVAL)
        assert uav.writes == 1

        # No more checks after a successful write
        await sleep(RETRY_INTERVAL * 3)
        assert uav.reads == 4
        nursery.cancel_scope.cancel()


async def test_lighthouse_lps_rewrites_configuration_changed_during_write(
    autojump_clock,
):
    uav = FakeUAV("01")
    lps = create_lps(uav)
    lps._set_configuration(create_configuration(0))
    new_config = create_configuration(0, 1)

    async with open_nursery() as nursery:
        nursery.start_soon(lps.keep_uav_in_sync, uav)

        # Fake UAV takes 0.1 seconds to read and 0.1 seconds to write
        await sleep(0.15)
        lps._set_configuration(new_config)
        await sleep(10)

        assert uav.writes == 2
        assert are_lighthouse_configurations_equal(uav.config, new_config)
        nursery.cancel_scope.cancel()


###########################################################################
# Importing the configuration from a drone


async def run_calibration(
    lps: LighthouseLocalPositioningSystem, params: dict[str, Any]
) -> list[Any]:
    events = []
    async for event in lps.calibrate(params):
        events.append(event)
    return events


async def test_lighthouse_lps_imports_configuration_from_drone(autojump_clock):
    config = create_configuration(0, 1)
    source = FakeUAV("01", config)
    lps = create_lps(source)

    events = await run_calibration(lps, {"method": "import", "uav": "01"})

    assert all(isinstance(event, Progress) for event in events)
    assert events[-1].percentage == 100
    assert lps.configuration == source.config
    assert [anchor.id for anchor in lps.anchors] == ["1", "2"]


async def test_lighthouse_lps_does_not_import_empty_configuration(autojump_clock):
    lps = create_lps(FakeUAV("01"))
    with raises(RuntimeError, match="geometry"):
        await run_calibration(lps, {"method": "import", "uav": "01"})
    assert lps.configuration is None


@mark.parametrize(
    "params,match",
    [
        ({}, "method"),
        ({"method": "estimate", "uav": "01"}, "method"),
        ({"method": "import"}, "ID of the UAV"),
        ({"method": "import", "uav": 1}, "ID of the UAV"),
        ({"method": "import", "uav": "02"}, "No such Crazyflie UAV"),
        ({"method": "import", "uav": "01", "foo": 1}, "Unknown calibration"),
    ],
)
async def test_lighthouse_lps_rejects_invalid_calibration_parameters(
    params: dict[str, Any], match: str
):
    lps = create_lps(FakeUAV("01", create_configuration(0)))
    with raises(RuntimeError, match=match):
        await run_calibration(lps, params)


###########################################################################
# Low-level functions that talk to a Crazyflie


class FakeLighthouse:
    def __init__(self, cf: "FakeCrazyflie"):
        self.cf = cf
        self.number_of_base_stations = 2

    async def clear_all(self):
        self.cf.calls.append(("clear_all", self.number_of_base_stations))
        if not self.cf.supported:
            raise ValueError("no memory matching type")

    async def get_configuration(self):
        self.cf.calls.append(("get_configuration", self.number_of_base_stations))
        if not self.cf.supported:
            raise ValueError("no memory matching type")
        return create_configuration(0)

    async def set_geometries(self, data):
        self.cf.calls.append(("set_geometries", sorted(data)))

    async def set_calibrations(self, data):
        self.cf.calls.append(("set_calibrations", sorted(data)))

    async def persist(self):
        self.cf.calls.append(("persist",))


class FakeCrazyflie:
    def __init__(self, supported: bool = True):
        self.calls: list[tuple[Any, ...]] = []
        self.lighthouse = FakeLighthouse(self)
        self.param = self
        self.supported = supported

    async def set(self, name: str, value: int):
        self.calls.append(("param", name, value))


async def test_read_lighthouse_configuration_reads_all_base_stations():
    cf = FakeCrazyflie()
    config = await read_lighthouse_configuration(cf)  # type: ignore
    assert config == create_configuration(0)
    assert cf.calls == [("get_configuration", 16)]


async def test_write_lighthouse_configuration_writes_partial_base_stations():
    cf = FakeCrazyflie()
    config = lighthouse_configuration_from_json(CFCLIENT_CONFIGURATION)  # type: ignore
    await write_lighthouse_configuration(cf, config)  # type: ignore
    assert cf.calls == [
        ("clear_all", 16),
        ("param", "lighthouse.systemType", 2),
        ("set_geometries", [0, 3]),
        ("set_calibrations", [0, 5]),
        ("param", "lighthouse.bsCalibReset", 1),
        ("persist",),
    ]


async def test_write_lighthouse_configuration_keeps_unknown_system_type():
    cf = FakeCrazyflie()
    config = create_configuration(0, system_type=LighthouseSystemType.UNKNOWN)
    await write_lighthouse_configuration(cf, config)  # type: ignore
    assert not any(call[:2] == ("param", "lighthouse.systemType") for call in cf.calls)


async def test_lighthouse_functions_report_missing_lighthouse_support():
    cf = FakeCrazyflie(supported=False)
    with raises(NotSupportedError):
        await read_lighthouse_configuration(cf)  # type: ignore
    with raises(NotSupportedError):
        await write_lighthouse_configuration(cf, create_configuration(0))  # type: ignore


###########################################################################
# Registration in the Crazyflie extension


async def test_extension_saves_and_restores_lighthouse_configuration(
    hub: RecordingMessageHub, tmp_path: Path
):
    async with running_crazyflie_extension(hub, tmp_path, "lighthouse") as (
        ext,
        _,
        driver,
    ):
        assert driver.lighthouse is not None
        path = ext.get_data_dir() / "lighthouse.json"
        assert driver.lighthouse.configuration_file == path
        assert path.is_relative_to(tmp_path)
        assert not path.exists()

        await driver.lighthouse.configure(CFCLIENT_CONFIGURATION)
        with path.open() as fp:
            assert json.load(fp) == CFCLIENT_CONFIGURATION

    # The configuration is restored after a restart
    async with running_crazyflie_extension(hub, tmp_path, "lighthouse") as (
        ext,
        _,
        driver,
    ):
        assert driver.lighthouse is not None
        assert await driver.lighthouse.get_configuration() == CFCLIENT_CONFIGURATION
        assert [anchor.id for anchor in driver.lighthouse.anchors] == ["1", "4", "6"]

        # Clearing the configuration deletes the file
        await driver.lighthouse.configure({})
        assert not path.exists()

    async with running_crazyflie_extension(hub, tmp_path, "lighthouse") as (
        _,
        _,
        driver,
    ):
        assert driver.lighthouse is not None
        assert driver.lighthouse.configuration is None


@mark.parametrize("contents", ["not JSON", '{"geos": {"16": {}}}'])
async def test_extension_ignores_invalid_saved_lighthouse_configuration(
    hub: RecordingMessageHub, tmp_path: Path, contents: str
):
    ext = CrazyflieDronesExtension()
    ext.app = SimpleNamespace(  # type: ignore
        dirs=SimpleNamespace(user_cache_path=tmp_path, user_data_path=tmp_path),
        uav_driver_registry=MagicMock(),
    )
    path = ext.get_data_dir() / "lighthouse.json"
    path.parent.mkdir(parents=True)
    path.write_text(contents)

    async with running_crazyflie_extension(hub, tmp_path, "lighthouse") as (
        ext,
        _,
        driver,
    ):
        assert driver.lighthouse is not None
        assert driver.lighthouse.configuration is None
        assert isinstance(ext.log, MagicMock)
        ext.log.error.assert_called_once()
        assert (
            "Ignoring invalid Lighthouse configuration"
            in (ext.log.error.call_args.args[0])
        )

    # The file is kept for the user to fix it
    assert path.read_text() == contents


async def test_extension_logs_errors_while_saving_lighthouse_configuration(
    hub: RecordingMessageHub, tmp_path: Path
):
    async with running_crazyflie_extension(hub, tmp_path, "lighthouse") as (
        ext,
        _,
        driver,
    ):
        # Make the data directory impossible to create
        ext.get_data_dir().parent.mkdir(parents=True)
        ext.get_data_dir().write_text("")

        assert driver.lighthouse is not None
        await driver.lighthouse.configure(CFCLIENT_CONFIGURATION)

        # Configuration is applied even if it could not be saved
        assert driver.lighthouse.configuration is not None
        assert isinstance(ext.log, MagicMock)
        ext.log.error.assert_called_once()
        assert "Error while saving" in ext.log.error.call_args.args[0]
