from enum import IntEnum, IntFlag
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
    INVALID = 8
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

    NAV_WAYPOINT = 16
    NAV_RETURN_TO_LAUNCH = 20
    NAV_LAND = 21
    NAV_TAKEOFF = 22
    PREFLIGHT_REBOOT_SHUTDOWN = 246
    COMPONENT_ARM_DISARM = 400
    SET_MESSAGE_INTERVAL = 511
    REQUEST_PROTOCOL_VERSION = 519
    REQUEST_AUTOPILOT_CAPABILITIES = 520


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


class MAVFrame(IntEnum):
    """Replica of the `MAV_FRAME` enum of the MAVLink protocol, using
    proper Python enums.

    Not all values are listed here, only the ones that we do actually use.
    """

    GLOBAL = 0
    LOCAL_NED = 1
    MISSION = 2
    GLOBAL_RELATIVE_ALT = 3
    LOCAL_ENU = 4
    GLOBAL_INT = 5
    GLOBAL_RELATIVE_ALT_INT = 6
    LOCAL_OFFSET_NED = 7


class MAVMessageType(IntEnum):
    """Enum containing some of the MAVLink message types that are important to
    us for some reason.
    """

    HEARTBEAT = 0
    SYS_STATUS = 1
    SYSTEM_TIME = 2
    REQUEST_DATA_STREAM = 66
    DATA_STREAM = 67
    COMMAND_INT = 75
    COMMAND_LONG = 76
    COMMAND_ACK = 77
    SET_POSITION_TARGET_GLOBAL_INT = 86
    AUTOPILOT_VERSION = 148


class MAVModeFlag(IntFlag):
    """Replica of the `MAV_MODE_FLAG` enum of the MAVLink protocol, using
    proper Python enums.
    """

    CUSTOM_MODE_ENABLED = 0x01
    TEST_ENABLED = 0x02
    AUTO_ENABLED = 0x04
    GUIDED_ENABLED = 0x08
    STABILIZE_ENABLED = 0x10
    HIL_ENABLED = 0x20
    MANUAL_INPUT_ENABLED = 0x40
    SAFETY_ARMED = 0x80


class MAVProtocolCapability(IntFlag):
    """Replica of the `MAV_PROTOCOL_CAPABILITY` enum of the MAVLink protocol,
    using proper Python enums.
    """

    MISSION_FLOAT = 0x01
    PARAM_FLOAT = 0x02
    MISSION_INT = 0x04
    COMMAND_INT = 0x08
    PARAM_UNION = 0x10
    FTP = 0x20
    SET_ATTITUDE_TARGET = 0x40
    SET_POSITION_TARGET_LOCAL_NED = 0x80
    SET_POSITION_TARGET_GLOBAL_INT = 0x100
    TERRAIN = 0x200
    SET_ACTUATOR_TARGET = 0x400
    FLIGHT_TERMINATION = 0x800
    COMPASS_CALIBRATION = 0x1000
    MAVLINK2 = 0x2000
    MISSION_FENCE = 0x4000
    MISSION_RALLY = 0x8000
    FLIGHT_INFORMATION = 0x10000


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


class MAVState(IntEnum):
    """Replica of the `MAV_STATE` enum of the MAVLink protocol, using proper
    Python enums.
    """

    UNINIT = 0
    BOOT = 1
    CALIBRATING = 2
    STANDBY = 3
    ACTIVE = 4
    CRITICAL = 5
    EMERGENCY = 6
    POWEROFF = 7
    FLIGHT_TERMINATION = 8


class MAVSysStatusSensor(IntFlag):
    """Replica of the `MAV_SYS_STATUS_SENSOR` flag set of the MAVLink protocol,
    using proper Python enums.
    """

    GYRO_3D = 1
    ACCEL_3D = 2
    MAG_3D = 4
    ABSOLUTE_PRESSURE = 8
    DIFFERENTIAL_PRESSURE = 0x10
    GPS = 0x20
    OPTICAL_FLOW = 0x40
    VISION_POSITION = 0x80
    LASER_POSITION = 0x100
    EXTERNAL_GROUND_TRUTH = 0x200
    ANGULAR_RATE_CONTROL = 0x400
    ATTITUDE_STABILIZATION = 0x800
    YAW_POSITION = 0x1000
    Z_ALTITUDE_CONTROL = 0x2000
    XY_POSITION_CONTROL = 0x4000
    MOTOR_OUTPUTS = 0x8000
    RC_RECEIVER = 0x10000
    GYRO2_3D = 0x20000
    ACCEL2_3D = 0x40000
    MAG2_3D = 0x80000
    GEOFENCE = 0x100000
    AHRS = 0x200000
    TERRAIN = 0x400000
    REVERSE_MOTOR = 0x800000
    LOGGING = 0x1000000
    BATTERY = 0x2000000
    PROXIMITY = 0x4000000
    SATCOM = 0x8000000
    PREARM_CHECK = 0x10000000
    OBSTACLE_AVOIDANCE = 0x20000000


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


class PositionTargetTypemask(IntFlag):
    """Replica of the `POSITION_TARGET_TYPEMASK` enum of the MAVLink protocol,
    using proper Python enums.
    """

    X_IGNORE = 0x01
    Y_IGNORE = 0x02
    Z_IGNORE = 0x04
    VX_IGNORE = 0x08
    VY_IGNORE = 0x10
    VZ_IGNORE = 0x20
    AX_IGNORE = 0x40
    AY_IGNORE = 0x80
    AZ_IGNORE = 0x100
    FORCE_SET = 0x200
    YAW_IGNORE = 0x400
    YAW_RATE_IGNORE = 0x800
