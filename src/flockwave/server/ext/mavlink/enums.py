from enum import IntEnum
from flockwave.server.model.gps import GPSFixType as OurGPSFixType

__all__ = ("MAVComponent",)


class MAVAutopilot(IntEnum):
    """Replica of the `MAV_AUTOPILOT` enum of the MAVLink protocol, using
    proper Python enums.
    """

    GENERIC = 0
    SLUGS = 2
    ARDUPILOTMEGA = 3
    OPENPILOT = 4
    GENERIC_WAYPOINTS_ONLY = 5
    GENERIC_WAYPOINTS_AND_SIMPLE_NAVIGATION_ONLY = 6
    GENERIC_MISSION_FULL = 7
    INVALUD = 8
    PPZ = 9
    UDB = 10
    FP = 11
    PX4 = 12
    SMACCMPILOT = 13
    AUTOQUAD = 14
    ARMAZILA = 15
    AEROB = 16
    ASLUAV = 17
    SMARTAP = 18
    AIRRAILS = 19


class MAVCommand(IntEnum):
    """Replica of the `MAV_CMD` enum of the MAVLink protocol, using proper
    Python enums.

    Not all values are listed here, only the ones that we do actually use.
    """

    PREFLIGHT_REBOOT_SHUTDOWN = 246
    SET_MESSAGE_INTERVAL = 511


class MAVComponent(IntEnum):
    """Replica of the `MAV_COMPONENT` enum of the MAVLink protocol, using proper
    Python enums.

    Not all values are listed here, only the ones that we do actually use.
    """

    AUTOPILOT1 = 1
    MISSIONPLANNER = 190


class MAVDataStream(IntEnum):
    """Replica of the `MAV_DATA_STREAM` enum of the MAVLink protocol, using
    proper Python enums.
    """

    ALL = 0
    RAW_SENSORS = 1
    EXTENDED_STATUS = 2
    RC_CHANNELS = 3
    RAW_CONTROLLER = 4
    POSITION = 6
    EXTRA1 = 10
    EXTRA2 = 11
    EXTRA3 = 12


class GPSFixType(IntEnum):
    """Replica of the `GPS_FIX_TYPE` enum of the MAVLink protocol, using
    proper Python enums.
    """

    NO_GPS = 0
    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3
    DGPS = 4
    RTK_FLOAT = 5
    RTK_FIXED = 6
    STATIC = 7
    PPP = 8

    def to_ours(self) -> OurGPSFixType:
        """Converts the MAVLink GPS fix type to our own GPS fix type enum."""
        return OurGPSFixType(min(self, GPSFixType.STATIC))


class MAVMessageType(IntEnum):
    """Enum containing some of the MAVLink message types that are important to
    us for some reason.
    """

    HEARTBEAT = 0
    SYSTEM_TIME = 2
    REQUEST_DATA_STREAM = 66
    DATA_STREAM = 67
    COMMAND_INT = 75
    COMMAND_LONG = 76
    COMMAND_ACK = 77


class MAVResult(IntEnum):
    """Replica of the `MAV_RESULT` enum of the MAVLink protocol, using proper
    Python enums.
    """

    ACCEPTED = 0
    TEMPORARILY_REJECTED = 1
    DENIED = 2
    UNSUPPORTED = 3
    FAILED = 4
    IN_PROGRESS = 5


class MAVType(IntEnum):
    """Replica of the `MAV_TYPE` enum of the MAVLink protocol, using proper
    Python enums.

    Not all values are listed here, only the ones that we do actually use.
    """

    GENERIC = 0
    FIXED_WING = 1
    QUADROTOR = 2
    ANTENNA_TRACKER = 5
    GCS = 6
    ONBOARD_CONTROLLER = 18
    GIMBAL = 26
    ADSB = 27
    CAMERA = 30
    CHARGING_STATION = 31
    FLARM = 32
    SERVO = 33
    ODID = 34

    def is_vehicle(self) -> bool:
        """Returns whether the MAVType constant denotes a vehicle (most likely)."""
        return int(self) < 34 and self not in (
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
        )
