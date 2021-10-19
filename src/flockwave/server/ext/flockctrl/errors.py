"""Error classes specific to the FlockCtrl extension."""

from __future__ import annotations

from flockwave.protocols.flockctrl.enums import StatusFlag
from flockwave.protocols.flockctrl.packets import StatusPacket
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.spec.errors import FlockwaveErrorCode
from typing import Any, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .driver import FlockCtrlUAV


__all__ = ("AddressConflictError", "get_error_codes_from_status_packet")


class FlockCtrlError(RuntimeError):
    """Base class for all error classes related to the FlockCtrl
    extension.
    """

    pass


class AddressConflictError(FlockCtrlError):
    """Error thrown when the driver receives a packet with a given UAV
    ID and a mismatching source address.
    """

    def __init__(self, uav: "FlockCtrlUAV", medium: str, address: Any):
        """Constructor.

        Parameters:
            uav: the UAV that the packet is addressed to,
                based on the UAV ID found in the packet
            medium: the communication medium on which the address is
                valid
            address: the source address where the packet came from
        """
        super(AddressConflictError, self).__init__(
            "Packet for UAV #{0.id} received from source address on "
            "communication medium {1!r} but the address does "
            "not belong to the UAV ({2!r})".format(uav, medium, address)
        )
        self.uav = uav
        self.medium = medium
        self.address = address


_error_code_mapping: Dict[int, Tuple[int, ...]] = {
    0: (),
    1: (FlockwaveErrorCode.HW_SW_INCOMPATIBLE.value,),
    2: (FlockwaveErrorCode.AUTOPILOT_COMM_TIMEOUT.value,),
    3: (FlockwaveErrorCode.AUTOPILOT_ACK_TIMEOUT.value,),
    4: (FlockwaveErrorCode.MAGNETIC_ERROR.value,),
    5: (FlockwaveErrorCode.GPS_SIGNAL_LOST.value,),
    6: (FlockwaveErrorCode.SENSOR_FAILURE.value,),
    7: (FlockwaveErrorCode.RC_SIGNAL_LOST_WARNING.value,),
    10: (FlockwaveErrorCode.GYROSCOPE_ERROR.value,),
    13: (FlockwaveErrorCode.ACCELEROMETER_ERROR.value,),
    16: (FlockwaveErrorCode.PRESSURE_SENSOR_ERROR.value,),
    18: (FlockwaveErrorCode.MOTOR_MALFUNCTION.value,),
    19: (FlockwaveErrorCode.AUTOPILOT_PROTOCOL_ERROR.value,),
    20: (FlockwaveErrorCode.UNSPECIFIED_CRITICAL_ERROR.value,),
    21: (FlockwaveErrorCode.AUTOPILOT_INIT_FAILED.value,),
    22: (FlockwaveErrorCode.AUTOPILOT_COMM_FAILED.value,),
    42: (FlockwaveErrorCode.SIMULATED_CRITICAL_ERROR.value,),
    43: (FlockwaveErrorCode.TARGET_NOT_FOUND.value,),
    44: (FlockwaveErrorCode.TARGET_TOO_FAR.value,),
    45: (FlockwaveErrorCode.REQUIRED_HW_COMPONENT_MISSING.value,),
    46: (FlockwaveErrorCode.BATTERY_CRITICAL.value,),
    47: (FlockwaveErrorCode.NO_GPS_HOME_POSITION.value,),
    48: (FlockwaveErrorCode.GEOFENCE_VIOLATION.value,),
    49: (FlockwaveErrorCode.GEOFENCE_VIOLATION.value,),
    50: (FlockwaveErrorCode.UNSPECIFIED_ERROR.value,),
    51: (FlockwaveErrorCode.CONTROL_ALGORITHM_ERROR.value,),
    52: (FlockwaveErrorCode.CONTROL_ALGORITHM_ERROR.value,),
    53: (FlockwaveErrorCode.EXTERNAL_CLOCK_ERROR.value,),
    54: (FlockwaveErrorCode.CONFIGURATION_ERROR.value,),
    55: (FlockwaveErrorCode.CONFIGURATION_ERROR.value,),
    56: (FlockwaveErrorCode.RC_NOT_CALIBRATED,),
    200: (FlockwaveErrorCode.LOGGING_DEACTIVATED.value,),
    201: (FlockwaveErrorCode.LOW_DISK_SPACE.value,),
    202: (FlockwaveErrorCode.TIMESYNC_ERROR.value,),
    203: (FlockwaveErrorCode.TIMESYNC_ERROR.value,),
}

_unspecified: Tuple[int, ...] = (FlockwaveErrorCode.UNSPECIFIED_ERROR,)


def get_error_codes_from_status_packet(
    packet: StatusPacket,
    preflight: PreflightCheckInfo,
) -> Tuple[int, ...]:
    """Maps an error code from FlockCtrl status and preflight packets
    to the corresponding Flockwave error code.

    Returns:
        FlockwaveErrorCode: the Flockwave error codes corresponding to the
            given FlockCtrl error code and flags
    """
    error_code = packet.error
    flags = packet.flags
    clock_status = packet.clock_status

    base = _error_code_mapping.get(error_code, _unspecified)
    aux: List[int] = []

    if not flags & StatusFlag.RADIO:
        aux.append(FlockwaveErrorCode.RADIO_MISSING.value)

    if flags & StatusFlag.LOGGING_FAILURE:
        aux.append(FlockwaveErrorCode.LOGGING_DEACTIVATED.value)

    if flags & StatusFlag.PREARM:
        aux.append(FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS.value)

        # TODO: move code below to separate function and call it from proper place
        # after prearm packet parsing, same way as in mavlink driver with ensure_errors()

        # check detailed prearm flags
        if preflight.get_result("TAKEOFF_PLACEMENT") == PreflightCheckResult.FAILURE:
            aux.append(FlockwaveErrorCode.FAR_FROM_TAKEOFF_POSITION.value)
        if preflight.get_result("RC") == PreflightCheckResult.FAILURE:
            aux.append(FlockwaveErrorCode.RC_SIGNAL_LOST_WARNING.value)
        if preflight.get_result("BATTERY") == PreflightCheckResult.FAILURE:
            aux.append(FlockwaveErrorCode.BATTERY_LOW_WARNING.value)

        # TODO: would be nice to have the followings:
        #   INVALID_MISSION_CONFIGURATION
        #   LOW_DISK_SPACE
        # TODO: prearm contains info for these also, if needed:
        #   NO_GPS_HOME_POSITION
        #   GEOFENCE_VIOLATION
        #   GPS_SIGNAL_LOST
        #   AUTOPILOT_COMM_FAILED
        #   SENSOR_FAILURE
        #   etc.

    if (
        flags
        & (
            StatusFlag.MOTOR_RUNNING
            | StatusFlag.AUTOPILOT_EMERGENCY
            | StatusFlag.AUTOPILOT_CRITICAL
            | StatusFlag.AIRBORNE
        )
        == StatusFlag.MOTOR_RUNNING
    ):
        aux.append(FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND.value)
    if not flags & StatusFlag.ARMED:
        aux.append(FlockwaveErrorCode.DISARMED.value)
    if flags & StatusFlag.TAKEOFF:
        aux.append(FlockwaveErrorCode.TAKEOFF.value)
    if flags & StatusFlag.LANDING:
        aux.append(FlockwaveErrorCode.LANDING.value)
    if (
        flags & (StatusFlag.MOTOR_RUNNING | StatusFlag.RETURN_TO_HOME)
        == StatusFlag.MOTOR_RUNNING | StatusFlag.RETURN_TO_HOME
    ):
        aux.append(FlockwaveErrorCode.RETURN_TO_HOME.value)
    if flags & StatusFlag.AUTOPILOT_INIT_PENDING:
        aux.append(FlockwaveErrorCode.AUTOPILOT_INITIALIZING.value)

    # timesync error is relevant only if we have at least 2D GPS fix
    if (
        (packet.location.lon or packet.location.lat)
        and clock_status
        and not clock_status.pps_timesync_achieved
    ):
        aux.append(FlockwaveErrorCode.TIMESYNC_ERROR)

    return base + tuple(aux) if aux else base
