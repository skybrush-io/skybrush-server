from flockwave.server.ext.mavlink.packets import (
    DroneShowExecutionStage,
    DroneShowStatus,
    DroneShowStatusFlag,
    authorization_scope_from_int,
    authorization_scope_to_int,
)
from flockwave.server.ext.show.config import AuthorizationScope
from flockwave.server.model.gps import GPSFixType


def test_authorization_scope_to_int():
    assert authorization_scope_to_int(AuthorizationScope.NONE) == 0
    assert authorization_scope_to_int(AuthorizationScope.LIVE) == 1
    assert authorization_scope_to_int(AuthorizationScope.REHEARSAL) == 2
    assert authorization_scope_to_int(AuthorizationScope.LIGHTS_ONLY) == 3
    assert authorization_scope_to_int("something_else") == 0  # type: ignore


def test_authorization_scope_from_int():
    assert authorization_scope_from_int(0) == AuthorizationScope.NONE
    assert authorization_scope_from_int(1) == AuthorizationScope.LIVE
    assert authorization_scope_from_int(2) == AuthorizationScope.REHEARSAL
    assert authorization_scope_from_int(3) == AuthorizationScope.LIGHTS_ONLY
    assert authorization_scope_from_int(4) == AuthorizationScope.NONE
    assert authorization_scope_from_int(-1) == AuthorizationScope.NONE
    assert authorization_scope_from_int("something_else") == AuthorizationScope.NONE  # type: ignore


def test_drone_show_status_from_bytes():
    # Legacy packt, length 9, no flags3 or elapsed_time field
    status = DroneShowStatus.from_bytes(b"\x01\x02\x03\x04\x05\x06\x07\x88\x19")

    assert status.start_time == 67305985
    assert status.light == 1541
    assert status.flags == (
        DroneShowStatusFlag.GEOFENCE_BREACHED
        | DroneShowStatusFlag.IS_GPS_TIME_BAD
        | DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START
        | DroneShowStatusFlag.IS_MISPLACED_BEFORE_TAKEOFF
    )
    assert status.stage is DroneShowExecutionStage.LANDED
    assert status.gps_fix is GPSFixType.NO_FIX
    assert status.num_satellites == 3
    assert status.authorization_scope is AuthorizationScope.LIVE

    # Legacy packet, same as above, but the authorization flag is cleared
    # and the "misplaced before takeoff" flag is also cleared
    status = DroneShowStatus.from_bytes(b"\x01\x02\x03\x04\x05\x06\x03\x08\x19")
    assert status.flags == (
        DroneShowStatusFlag.GEOFENCE_BREACHED | DroneShowStatusFlag.IS_GPS_TIME_BAD
    )
    assert status.authorization_scope is AuthorizationScope.NONE

    # v2 packet, length 12
    status = DroneShowStatus.from_bytes(
        b"\x01\x02\x03\x04\x05\x06\x07\x88\x19\x8f\x0a\x0b"
    )

    assert status.start_time == 67305985
    assert status.light == 1541
    assert status.flags == (
        DroneShowStatusFlag.GEOFENCE_BREACHED
        | DroneShowStatusFlag.IS_GPS_TIME_BAD
        | DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START
        | DroneShowStatusFlag.IS_MISPLACED_BEFORE_TAKEOFF
        | DroneShowStatusFlag.IS_FAR_FROM_EXPECTED_POSITION
    )
    assert status.stage is DroneShowExecutionStage.LANDED
    assert status.gps_fix is GPSFixType.NO_FIX
    assert status.num_satellites == 3
    assert status.authorization_scope is AuthorizationScope.LIGHTS_ONLY
    assert status.elapsed_time == 2826
