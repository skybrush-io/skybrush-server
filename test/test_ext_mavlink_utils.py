"""Tests for the utility functions of the MAVLink extension."""

from pytest import mark

from flockwave.server.ext.mavlink.enums import MAVType
from flockwave.server.ext.mavlink.utils import is_mavlink_vehicle


@mark.parametrize(
    "type",
    [
        MAVType.GENERIC,
        MAVType.FIXED_WING,
        MAVType.QUADROTOR,
        MAVType.COAXIAL,
        MAVType.HELICOPTER,
        MAVType.GROUND_ROVER,
        MAVType.SURFACE_BOAT,
        MAVType.SUBMARINE,
        MAVType.HEXAROTOR,
        MAVType.OCTOROTOR,
        MAVType.TRICOPTER,
        MAVType.VTOL_TAILSITTER_DUOROTOR,
        MAVType.VTOL_TAILSITTER_QUADROTOR,
        MAVType.VTOL_TILTROTOR,
        MAVType.VTOL_TILTWING,
        MAVType.DODECAROTOR,
        MAVType.DECAROTOR,
        MAVType.GENERIC_MULTIROTOR,
        MAVType.SPACECRAFT_ORBITER,
        MAVType.GROUND_QUADRUPED,
        MAVType.VTOL_GYRODYNE,
    ],
)
def test_is_mavlink_vehicle_for_vehicles(type: MAVType):
    assert is_mavlink_vehicle(type)
    assert is_mavlink_vehicle(int(type))
    assert type.is_vehicle


@mark.parametrize(
    "type",
    [
        MAVType.ANTENNA_TRACKER,
        MAVType.GCS,
        MAVType.ONBOARD_CONTROLLER,
        MAVType.GIMBAL,
        MAVType.ADSB,
        MAVType.CAMERA,
        MAVType.CHARGING_STATION,
        MAVType.FLARM,
        MAVType.SERVO,
        MAVType.ODID,
        MAVType.BATTERY,
        MAVType.PARACHUTE,
        MAVType.LOG,
        MAVType.OSD,
        MAVType.IMU,
        MAVType.GPS,
        MAVType.WINCH,
        MAVType.ILLUMINATOR,
        MAVType.GRIPPER,
        MAVType.RADIO,
    ],
)
def test_is_mavlink_vehicle_for_non_vehicles(type: MAVType):
    assert not is_mavlink_vehicle(type)
    assert not is_mavlink_vehicle(int(type))
    assert not type.is_vehicle


@mark.parametrize("type", [-1, 50, 100, 255, 256, 1000])
def test_is_mavlink_vehicle_for_unknown_or_out_of_range_types(type: int):
    assert not is_mavlink_vehicle(type)
