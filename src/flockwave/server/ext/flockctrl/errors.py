"""Error classes specific to the FlockCtrl extension."""

from flockwave.server.model.errors import FlockwaveErrorCode


__all__ = ("ParseError", "AddressConflictError", "map_flockctrl_error_code")


class FlockCtrlError(RuntimeError):
    """Base class for all error classes related to the FlockCtrl
    extension.
    """

    pass


class ParseError(FlockCtrlError):
    """Error thrown when the parser failed to parse a FlockCtrl packet."""

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
    0: FlockwaveErrorCode.NO_ERROR,
    1: FlockwaveErrorCode.HW_SW_INCOMPATIBLE,
    2: FlockwaveErrorCode.AUTOPILOT_COMM_TIMEOUT,
    3: FlockwaveErrorCode.AUTOPILOT_ACK_TIMEOUT,
    4: FlockwaveErrorCode.MAGNETIC_ERROR,
    5: FlockwaveErrorCode.GPS_SIGNAL_LOST,
    6: FlockwaveErrorCode.SENSOR_FAILURE,
    7: FlockwaveErrorCode.RC_SIGNAL_LOST_WARNING,
    10: FlockwaveErrorCode.GYROSCOPE_ERROR,
    13: FlockwaveErrorCode.ACCELEROMETER_ERROR,
    16: FlockwaveErrorCode.PRESSURE_SENSOR_ERROR,
    18: FlockwaveErrorCode.MOTOR_MALFUNCTION,
    19: FlockwaveErrorCode.AUTOPILOT_PROTOCOL_ERROR,
    20: FlockwaveErrorCode.UNSPECIFIED_CRITICAL_ERROR,
    21: FlockwaveErrorCode.AUTOPILOT_INIT_FAILED,
    22: FlockwaveErrorCode.AUTOPILOT_COMM_FAILED,
    42: FlockwaveErrorCode.SIMULATED_CRITICAL_ERROR,
    43: FlockwaveErrorCode.TARGET_NOT_FOUND,
    44: FlockwaveErrorCode.TARGET_TOO_FAR,
    45: FlockwaveErrorCode.REQUIRED_HW_COMPONENT_MISSING,
    46: FlockwaveErrorCode.BATTERY_CRITICAL,
    47: FlockwaveErrorCode.NO_GPS_HOME_POSITION,
    48: FlockwaveErrorCode.GEOFENCE_VIOLATION,
    49: FlockwaveErrorCode.GEOFENCE_VIOLATION,
    50: FlockwaveErrorCode.UNSPECIFIED_ERROR,
    51: FlockwaveErrorCode.CONTROL_ALGORITHM_ERROR,
    52: FlockwaveErrorCode.CONTROL_ALGORITHM_ERROR,
    53: FlockwaveErrorCode.EXTERNAL_CLOCK_ERROR,
    54: FlockwaveErrorCode.CONFIGURATION_ERROR,
    200: FlockwaveErrorCode.LOGGING_DEACTIVATED,
    201: FlockwaveErrorCode.LOW_DISK_SPACE,
    202: FlockwaveErrorCode.TIMESYNC_ERROR,
    203: FlockwaveErrorCode.TIMESYNC_ERROR,
}


def map_flockctrl_error_code(error_code):
    """Maps an error code from a FlockCtrl status packet to the corresponding
    Flockwave error code.

    Returns:
        FlockwaveErrorCode: the Flockwave error code corresponding to the
            given FlockCtrl error code
    """
    return _error_code_mapping.get(error_code, FlockwaveErrorCode.UNSPECIFIED_ERROR)
