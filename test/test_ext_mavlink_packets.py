from flockwave.server.ext.mavlink.packets import (
    DroneShowExecutionStage,
    DroneShowStatus,
    DroneShowStatusFlag,
    authorization_scope_from_int,
    authorization_scope_to_int,
    create_start_time_configuration_packet,
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
    # Legacy packet, length 9, no flags3 or elapsed_time field
    status = DroneShowStatus.from_bytes(b"\x01\x02\x03\x04\x05\x06\x07\x88\x19")

    assert status.start_time_msec == 197121064
    assert status.start_time_sec == 197121.064
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
        b"\x01\x02\x03\x04\x05\x06\x07\x88\x19\xcf\x0a\x0b"
    )

    assert status.start_time_msec == 197121064
    assert status.start_time_sec == 197121.064
    assert status.light == 1541
    assert status.flags == (
        DroneShowStatusFlag.GEOFENCE_BREACHED
        | DroneShowStatusFlag.IS_GPS_TIME_BAD
        | DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START
        | DroneShowStatusFlag.IS_MISPLACED_BEFORE_TAKEOFF
        | DroneShowStatusFlag.IS_FAR_FROM_EXPECTED_POSITION
        | DroneShowStatusFlag.HAS_HIGH_ESC_ERROR_RATE
    )
    assert status.stage is DroneShowExecutionStage.LANDED
    assert status.gps_fix is GPSFixType.NO_FIX
    assert status.num_satellites == 3
    assert status.authorization_scope is AuthorizationScope.LIGHTS_ONLY
    assert status.elapsed_time == 2826
    assert status.has_high_esc_error_rate


def test_create_start_time_configuration_packet():
    packet_type, kwargs = create_start_time_configuration_packet(
        AuthorizationScope.LIVE, should_update_takeoff_time=False
    )
    assert packet_type == "DATA16"
    assert (
        kwargs["data"]
        == b"\x01\xff\xff\xff\x7f\x01\xff\xff\xff\x7f\x00\x00\x00\x00\x00\x00"
    )
    assert kwargs["len"] == 12
    assert kwargs["type"] == 92

    packet_type, kwargs = create_start_time_configuration_packet(
        AuthorizationScope.LIGHTS_ONLY, should_update_takeoff_time=True
    )
    assert packet_type == "DATA16"
    assert (
        kwargs["data"]
        == b"\x01\x00\x00\x00\x80\x03\x00\x00\x00\x80\x00\x00\x00\x00\x00\x00"
    )
    assert kwargs["len"] == 12
    assert kwargs["type"] == 92

    packet_type, kwargs = create_start_time_configuration_packet(
        AuthorizationScope.LIGHTS_ONLY,
        start_time=1778710848.543,
        should_update_takeoff_time=True,
    )
    assert packet_type == "DATA16"
    # mask the countdown because that depends on the current time
    kwargs["data"] = kwargs["data"][:6] + b"\xde\xad\xbe\xef" + kwargs["data"][10:]
    assert (
        kwargs["data"]
        == b"\x01\xd2\x2e\x05\x00\x03\xde\xad\xbe\xef\x1f\x02\x00\x00\x00\x00"
    )
    assert kwargs["len"] == 12
    assert kwargs["type"] == 92
