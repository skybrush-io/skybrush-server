"""Error classes specific to the Flockwave model."""

from builtins import str
from enum import IntEnum


__all__ = ("ClientNotSubscribedError", "NoSuchPathError")


class FlockwaveError(RuntimeError):
    """Base class for all error classes related to the Flockwave model."""

    pass


class ClientNotSubscribedError(FlockwaveError):
    """Error thrown when a client attempts to unsubscribe from a part of the
    device tree that it is not subscribed to.
    """

    def __init__(self, client, path):
        """Constructor.

        Parameters:
            client (Client): the client that attempted to unsubscribe
            path (DeviceTreePath): the path that the client attempted to
                unsubscribe from
        """
        super(ClientNotSubscribedError, self).__init__(str(client))
        self.client = client
        self.path = path


class NoSuchPathError(FlockwaveError):
    """Error thrown when the device tree failed to resolve a device tree
    path to a corresponding node.
    """

    def __init__(self, path):
        """Constructor.

        Parameters:
            path (DeviceTreePath): the path that could not be resolved into
                a node
        """
        super(NoSuchPathError, self).__init__(str(path))
        self.path = path


#: Dictionary mapping Flockwave error codes to more-or-less human-readable
#: abbreviations
_error_code_to_abbreviation = {
    0: "ok",
    1: "disarm",
    2: "no log",
    3: "prearm",
    4: "init",
    5: "takeoff",
    6: "landing",
    7: "landed",
    64: "storage",
    65: "RC lost",
    66: "lowbat",
    67: "timesync",
    128: "comm t/o",
    129: "ack t/o",
    130: "proto",
    131: "prearm",
    132: "RC lost",
    133: "no GPS",
    134: "lowbat",
    135: "target",
    136: "too far",
    137: "config",
    188: "simerr",
    189: "control",
    190: "sensor",
    191: "error",
    192: "compat",
    193: "mag",
    194: "gyro",
    195: "acc",
    196: "baro",
    197: "GPS",
    198: "motor",
    199: "lowbat",
    200: "home",
    201: "fence",
    202: "clk",
    203: "extclk",
    204: "no HW",
    205: "initfail",
    206: "commfail",
    207: "crash",
    253: "simcrit",
    254: "sensor",
    255: "fatal",
}


#: Dictionary mapping Flockwave error codes to human-readable descriptions.
_error_code_to_description = {
    0: "No error",
    1: "Drone not armed yet",
    2: "Logging deactivated",
    3: "Prearm check in progress",
    4: "Autopilot initializing",
    5: "Drone is taking off",
    6: "Drone is landing",
    7: "Drone has landed successfully",
    64: "Low disk space",
    65: "RC lost",
    66: "Battery low",
    67: "Timesync error",
    128: "Autopilot communication timeout",
    129: "Autopilot acknowledgment timeout",
    130: "Autopilot communication protocol error",
    131: "Prearm check failure",
    132: "RC signal lost",
    133: "GPS signal lost or GPS error",
    134: "Battery low",
    135: "Target not found",
    136: "Target is too far",
    137: "Configuration error",
    188: "Simulated error",
    189: "Error in control algorithm",
    190: "Unspecified sensor failure",
    191: "Unspecified error",
    192: "Incompatible hardware or software",
    193: "Magnetometer error",
    194: "Gyroscope error",
    195: "Accelerometer error",
    196: "Pressure sensor or altimeter error",
    197: "GPS error or GPS signal lost",
    198: "Motor malfunction",
    199: "Battery critical",
    200: "No GPS home position",
    201: "Geofence violation",
    202: "Internal clock error",
    203: "External clock error",
    204: "Required hardware component missing",
    205: "Autopilot initialization failed",
    206: "Autopilot communication failed",
    207: "Drone crashed",
    253: "Simulated critical error",
    254: "Unspecified critical sensor failure",
    255: "Unspecified critical error",
}


class FlockwaveErrorCode(IntEnum):
    """Error codes defined in the Flockwave protocol."""

    NO_ERROR = 0

    # Informational messages
    DISARMED = 1
    LOGGING_DEACTIVATED = 2
    PREARM_CHECK_IN_PROGRESS = 3
    AUTOPILOT_INITIALIZING = 4
    TAKEOFF = 5
    LANDING = 6
    LANDED = 7

    # Warnings
    LOW_DISK_SPACE = 64
    RC_SIGNAL_LOST_WARNING = 65
    BATTERY_LOW_WARNING = 66
    TIMESYNC_ERROR = 67

    # Errors
    AUTOPILOT_COMM_TIMEOUT = 128
    AUTOPILOT_ACK_TIMEOUT = 129
    AUTOPILOT_PROTOCOL_ERROR = 130
    PREARM_CHECK_FAILURE = 131
    RC_SIGNAL_LOST_ERROR = 132
    GPS_SIGNAL_LOST = 133
    BATTERY_LOW_ERROR = 134
    TARGET_NOT_FOUND = 135
    TARGET_TOO_FAR = 136
    CONFIGURATION_ERROR = 137
    SIMULATED_ERROR = 188
    CONTROL_ALGORITHM_ERROR = 189
    SENSOR_FAILURE = 190
    UNSPECIFIED_ERROR = 191

    # Critical errors
    HW_SW_INCOMPATIBLE = 192
    MAGNETIC_ERROR = 193
    GYROSCOPE_ERROR = 194
    ACCELEROMETER_ERROR = 195
    PRESSURE_SENSOR_ERROR = 196
    GPS_SIGNAL_LOST_CRITICAL = 197
    MOTOR_MALFUNCTION = 198
    BATTERY_CRITICAL = 199
    NO_GPS_HOME_POSITION = 200
    GEOFENCE_VIOLATION = 201
    INTERNAL_CLOCK_ERROR = 202
    EXTERNAL_CLOCK_ERROR = 203
    REQUIRED_HW_COMPONENT_MISSING = 204
    AUTOPILOT_INIT_FAILED = 205
    AUTOPILOT_COMM_FAILED = 206
    CRASH = 207
    SIMULATED_CRITICAL_ERROR = 253
    CRITICAL_SENSOR_FAILURE = 254
    UNSPECIFIED_CRITICAL_ERROR = 255

    @property
    def abbreviation(self):
        """Returns a short abbreviation of the error code that is more-or-less
        human-readable, but requires less space on the screen than the full
        description of the error.
        """
        return _error_code_to_abbreviation.get(self) or f"E{self}"

    @property
    def description(self):
        """Returns a human-readable description of the error code."""
        return _error_code_to_description.get(self) or f"Error {self}"
