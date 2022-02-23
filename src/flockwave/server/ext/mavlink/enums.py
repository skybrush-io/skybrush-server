from enum import IntEnum, IntFlag
from flockwave.server.model.gps import GPSFixType as OurGPSFixType
from struct import Struct
from typing import Union

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
    DO_SET_MODE = 176
    DO_REPOSITION = 192
    DO_MOTOR_TEST = 209
    PREFLIGHT_CALIBRATION = 241
    PREFLIGHT_REBOOT_SHUTDOWN = 246
    COMPONENT_ARM_DISARM = 400
    SET_MESSAGE_INTERVAL = 511
    REQUEST_PROTOCOL_VERSION = 519
    REQUEST_AUTOPILOT_CAPABILITIES = 520
    REQUEST_CAMERA_INFORMATION = 521

    NAV_FENCE_RETURN_POINT = 5000
    NAV_FENCE_POLYGON_VERTEX_INCLUSION = 5001
    NAV_FENCE_POLYGON_VERTEX_EXCLUSION = 5002
    NAV_FENCE_CIRCLE_INCLUSION = 5003
    NAV_FENCE_CIRCLE_EXCLUSION = 5004
    NAV_RALLY_POINT = 5100

    WAYPOINT_USER_1 = 31000
    WAYPOINT_USER_2 = 31001
    WAYPOINT_USER_3 = 31002
    WAYPOINT_USER_4 = 31003
    WAYPOINT_USER_5 = 31004
    SPATIAL_USER_1 = 31005
    SPATIAL_USER_2 = 31006
    SPATIAL_USER_3 = 31007
    SPATIAL_USER_4 = 31008
    SPATIAL_USER_5 = 31009
    USER_1 = 31010
    USER_2 = 31011
    USER_3 = 31012
    USER_4 = 31013
    USER_5 = 31014

    # ArduPilot-specific commands
    DO_START_MAG_CAL = 42424


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
    GPS_RAW_INT = 24
    GLOBAL_POSITION_INT = 33
    REQUEST_DATA_STREAM = 66
    DATA_STREAM = 67
    COMMAND_INT = 75
    COMMAND_LONG = 76
    COMMAND_ACK = 77
    SET_POSITION_TARGET_GLOBAL_INT = 86
    AUTOPILOT_VERSION = 148
    MAG_CAL_PROGRESS = 191  # ArduPilot-specific
    MAG_CAL_REPORT = 192


class MAVMissionResult(IntEnum):
    """Replica of the `MAV_MISSION_RESULT` enum of the MAVLink protocol, using
    proper Python enums.
    """

    ACCEPTED = 0
    ERROR = 1
    UNSUPPORTED_FRAME = 2
    UNSUPPORTED = 3
    NO_SPACE = 4
    INVALID = 5
    INVALID_PARAM1 = 6
    INVALID_PARAM2 = 7
    INVALID_PARAM3 = 8
    INVALID_PARAM4 = 9
    INVALID_PARAM5_X = 10
    INVALID_PARAM6_Y = 11
    INVALID_PARAM7 = 12
    INVALID_SEQUENCE = 13
    DENIED = 14
    OPERATION_CANCELLED = 15


class MAVMissionType(IntEnum):
    """Replica of the `MAV_MISSION_TYPE` enum of the MAVLink protocol, using
    proper Python enums.
    """

    MISSION = 0
    FENCE = 1
    RALLY = 2
    ALL = 255


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


_mav_param_type_structs = [
    Struct(spec) if spec else None
    for spec in (
        None,
        ">xxxB",
        ">xxxb",
        ">xxH",
        ">xxh",
        ">I",
        ">i",
        ">Q",
        ">q",
        ">f",
        ">d",
    )
]


class MAVParamType(IntEnum):
    """Replica of the `MAV_PARAM_TYPE` enum of the MAVLink protocol, using
    proper Python enums.
    """

    UINT8 = 1
    INT8 = 2
    UINT16 = 3
    INT16 = 4
    UINT32 = 5
    INT32 = 6
    UINT64 = 7
    INT64 = 8
    REAL32 = 9
    REAL64 = 10

    def as_float(self, value) -> float:
        """Encodes the given value as this MAVLink parameter type, ready to be
        transferred to the remote end encoded as a float.

        This is a quirk of the MAVLink parameter protocol where the official,
        over-the-wire type of each parameter is a float, but sometimes we want to
        transfer, say 32-bit integers. In this case, the 32-bit integer
        representation is _reinterpreted_ as a float, and the resulting float value
        is sent over the wire; the other side will then _reinterpret_ it again as
        a 32-bit integer.

        For example, when we want to transfer 474832328 as an integer, this cannot
        be represented accurately as a single-precision float (the nearest float
        that can be represented is 474832320 = 1.768888235092163 x 2^28).
        Therefore, we take the bitwise representation of 474832328 (i.e.
        0x1c4d5dc8), and treat it as a float directly instead (think about casting
        an `int32_t*` to a `float*` directly in C). This gives us
        6.795001965406856...e-22, whose bitwise representation is identical to
        0x1c4d5dc8.
        """
        if self is MAVParamType.REAL32:
            return float(value)
        elif self is MAVParamType.REAL64:
            return float(value)
        else:
            encoded = _mav_param_type_structs[self].pack(value)
            return _mav_param_type_structs[MAVParamType.REAL32].unpack(encoded)[0]

    def decode_float(self, value: float) -> Union[int, float]:
        """Decodes the given value by interpreting it as this MAVLink parameter
        type.

        This function is the opposite of `encode_float()`; see its documentation
        for more details.
        """
        if self is MAVParamType.REAL32:
            return float(value)
        elif self is MAVParamType.REAL64:
            return float(value)
        else:
            encoded = _mav_param_type_structs[MAVParamType.REAL32].pack(value)
            return _mav_param_type_structs[self].unpack(encoded)[0]


class MAVProtocolCapability(IntFlag):
    """Replica of the `MAV_PROTOCOL_CAPABILITY` enum of the MAVLink protocol,
    using proper Python enums.
    """

    MISSION_FLOAT = 0x01
    PARAM_FLOAT = 0x02
    # MISSION_INT = 0x04    # deprecated (2020-06)
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

    # Skybrush-specific extension
    DRONE_SHOW_MODE = 0x4000000


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
    HEXAROTOR = 13
    OCTOROTOR = 14
    TRICOPTER = 15
    ONBOARD_CONTROLLER = 18
    GIMBAL = 26
    ADSB = 27
    DODECAROTOR = 29
    CAMERA = 30
    CHARGING_STATION = 31
    FLARM = 32
    SERVO = 33
    ODID = 34
    DECAROTOR = 35

    def is_vehicle(self) -> bool:
        """Returns whether the MAVType constant denotes a vehicle (most likely)."""
        return int(self) < 36 and self not in (
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

    @property
    def motor_count(self) -> int:
        """Returns the best estimate of the motor count associated with the
        given MAVType or 4 as a default."""
        if self == MAVType.DODECAROTOR:
            return 12
        if self == MAVType.DECAROTOR:
            return 10
        if self == MAVType.OCTOROTOR:
            return 8
        if self == MAVType.HEXAROTOR:
            return 6
        if self == MAVType.TRICOPTER:
            return 3
        return 4


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


class MotorTestOrder(IntEnum):
    """Replica of the `MOTOR_TEST_ORDER` enum of the MAVLink protocol,
    using proper Python enums.
    """

    DEFAULT = 0
    SEQUENCE = 1
    BOARD = 2


class MotorTestThrottleType(IntEnum):
    """Replica of the `MOTOR_TEST_THROTTLE_TYPE` enum of the MAVLink protocol,
    using proper Python enums.
    """

    PERCENT = 0
    PWM = 1
    PILOT = 2
    CAL = 3


class MagCalStatus(IntEnum):
    """Replica of the `MAG_CAL_STATUS` enum of the MAVLink protocol, using
    proper Python enums.
    """

    NOT_STARTED = 0
    WAITING_TO_START = 1
    RUNNING_STEP_ONE = 2
    RUNNING_STEP_TWO = 3
    SUCCESS = 4
    FAILED = 5
    BAD_ORIENTATION = 6
    BAD_RADIUS = 7

    @property
    def is_calibrating(self) -> bool:
        return self >= 1 and self <= 3

    @property
    def is_failure(self) -> bool:
        return self >= 5

    @property
    def is_successful(self) -> bool:
        return self == 4
