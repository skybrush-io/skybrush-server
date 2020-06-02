"""Error classes specific to the FlockCtrl extension."""

from flockwave.protocols.flockctrl.enums import StatusFlag
from flockwave.spec.errors import FlockwaveErrorCode
from typing import Tuple, Union


__all__ = ("AddressConflictError", "map_flockctrl_error_code_and_flags")


class FlockCtrlError(RuntimeError):
    """Base class for all error classes related to the FlockCtrl
    extension.
    """

    pass


class AddressConflictError(FlockCtrlError):
    """Error thrown when the driver receives a packet with a given UAV
    ID and a mismatching source address.
    """

    def __init__(self, uav, medium, address):
        """Constructor.

        Parameters:
            uav (FlockCtrlUAV): the UAV that the packet is addressed to,
                based on the UAV ID found in the packet
            medium (str): the communication medium on which the address is
                valid
            address (object): the source address where the packet came from
        """
        super(AddressConflictError, self).__init__(
            "Packet for UAV #{0.id} received from source address on "
            "communication medium {1!r} but the address does "
            "not belong to the UAV ({2!r})".format(uav, medium, address)
        )
        self.uav = uav
        self.medium = medium
        self.address = address


_error_code_mapping = {
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
    200: (FlockwaveErrorCode.LOGGING_DEACTIVATED.value,),
    201: (FlockwaveErrorCode.LOW_DISK_SPACE.value,),
    202: (FlockwaveErrorCode.TIMESYNC_ERROR.value,),
    203: (FlockwaveErrorCode.TIMESYNC_ERROR.value,),
}


def map_flockctrl_error_code_and_flags(
    error_code: int, flags: int = 0
) -> Union[Tuple[FlockwaveErrorCode], Tuple[()]]:
    """Maps an error code from a FlockCtrl status packet to the corresponding
    Flockwave error code.

    Returns:
        FlockwaveErrorCode: the Flockwave error codes corresponding to the
            given FlockCtrl error code and flags
    """
    base = _error_code_mapping.get(error_code, FlockwaveErrorCode.UNSPECIFIED_ERROR)
    aux = []

    if flags & StatusFlag.PREARM:
        aux.append(FlockwaveErrorCode.PREARM_CHECK_IN_PROGRESS.value)
    if (
        flags & (StatusFlag.MOTOR_RUNNING | StatusFlag.ON_GROUND)
        == StatusFlag.MOTOR_RUNNING | StatusFlag.ON_GROUND
    ):
        aux.append(FlockwaveErrorCode.MOTORS_RUNNING_WHILE_ON_GROUND.value)
    if not flags & StatusFlag.ARMED:
        aux.append(FlockwaveErrorCode.DISARMED)

    return base + tuple(aux) if aux else base
