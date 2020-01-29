"""Error classes specific to the Flockwave model."""

from builtins import str
from enum import Enum


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


class FlockwaveErrorCode(Enum):
    """Error codes defined in the Flockwave protocol."""

    NO_ERROR = 0

    # Informational messages
    LOGGING_DEACTIVATED = 1
    AUTOPILOT_INITIALIZING = 2
    PREARM_CHECK_IN_PROGRESS = 3

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
    SIMULATED_CRITICAL_ERROR = 253
    CRITICAL_SENSOR_FAILURE = 254
    UNSPECIFIED_CRITICAL_ERROR = 255
