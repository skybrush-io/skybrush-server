from dataclasses import dataclass
from enum import IntEnum, IntFlag
from functools import lru_cache
from struct import Struct

from flockwave.gps.time import gps_time_of_week_to_utc
from flockwave.server.model.gps import GPSFixType as OurGPSFixType

from .enums import GPSFixType
from .types import MAVLinkMessage

__all__ = ("DroneShowStatus",)


@lru_cache(maxsize=128)
def format_gps_time_of_week(value: int) -> str:
    # TODO(ntamas): the cache should be invalidated every week, but at the
    # moment it is unlikely that the server keeps on running for more than a
    # week without interruptions, so let's just ignore this for the time being.
    # If we really need to, we can call format_gps_time_of_week.cache_clear()
    # regularly.
    if value < 0:
        return "--:--:--"
    else:
        dt = gps_time_of_week_to_utc(value)
        return dt.strftime("%H:%M:%S")


class DroneShowStatusFlag(IntFlag):
    """Status flags used in the Skybrush-specific drone show status packet."""

    HAS_SHOW_FILE = 1 << 7
    HAS_START_TIME = 1 << 6
    HAS_ORIGIN = 1 << 5
    HAS_ORIENTATION = 1 << 4
    HAS_GEOFENCE = 1 << 3


_stage_descriptions = {
    0: "",
    1: "Initializing...",
    2: "Waiting for start time",
    3: "Taking off",
    4: "Performing show",
    5: "Return to launch",
    6: "Loitering",
    7: "Landing",
    8: "Landed",
    9: "Error",
}


class DroneShowExecutionStage(IntEnum):
    """Execution stage constants in the Skybrush-specific drone show status
    packet.
    """

    UNKNOWN = -1
    OFF = 0
    INIT = 1
    WAIT_FOR_START_TIME = 2
    TAKEOFF = 3
    PERFORMING = 4
    RTL = 5
    LOITER = 6
    LANDING = 7
    LANDED = 8
    ERROR = 9

    @property
    def probably_airborne(self) -> bool:
        """Returns whether the drone is probably airborne while performing
        this stage.
        """
        return 3 <= self <= 7

    @property
    def has_error(self) -> bool:
        """returns whether the execution stage represents an error condition."""
        return self < 0 or self == 9

    @property
    def description(self) -> str:
        """Returns a human-readable description of the stage."""
        description = _stage_descriptions.get(self)
        description = description or "Unknown stage {self}"
        return description


@dataclass
class DroneShowStatus:
    """Data class representing a Skybrush-specific drone show status object."""

    start_time: int = 0
    flags: DroneShowStatusFlag = 0
    stage: DroneShowExecutionStage = DroneShowExecutionStage.OFF
    light: int = 0
    gps_fix: GPSFixType = OurGPSFixType.NO_GPS
    num_satellites: int = 0

    #: Identifier of Skybrush-specific DATA16 show status packets
    TYPE = 0x5B

    #: Structure of Skybrush-specific DATA16 show status packets
    _struct = Struct("<iHBBB")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw body of a MAVLink
        DATA16 packet that has already been truncated to the desired length of
        the packet.
        """
        start_time, light, flags, stage, gps_health = cls._struct.unpack(data[:9])

        try:
            stage = DroneShowExecutionStage(stage)
        except Exception:
            stage = DroneShowExecutionStage.UNKNOWN

        return cls(
            start_time=start_time,
            light=light,
            flags=flags,
            stage=stage,
            gps_fix=GPSFixType.to_ours(gps_health & 0x07),
            num_satellites=gps_health >> 3,
        )

    @classmethod
    def from_mavlink_message(cls, message: MAVLinkMessage):
        """Constructs a DroneShowStatus_ object from a MAVLink DATA16 packet.

        Raises:
            ValueError: if the type of the MAVLink DATA16 packet does not match
                the expected type of a Skybrush-specific show status packet
        """
        if message.type != cls.TYPE:
            raise ValueError(
                f"type of MAVLink packet is {message.type}, expected {cls.TYPE}"
            )

        return cls.from_bytes(bytes(message.data[: message.len]))

    @property
    def message(self) -> str:
        """Returns a short status message string that can be used for reporting
        the status of the drone show subsystem on the UI.
        """
        start_time = format_gps_time_of_week(self.start_time)
        message = self._format_message_from_stage_and_flags(self.stage, self.flags)
        return f"[{start_time}] {message}"

    @staticmethod
    def _format_message_from_stage_and_flags(
        stage: DroneShowExecutionStage, flags: DroneShowStatusFlag
    ) -> str:
        """Formats a status message from the execution stage and flags found in
        a drone show status packet.
        """
        if not flags & DroneShowStatusFlag.HAS_SHOW_FILE:
            return "No show data"

        # If we are in a stage that implies that we are flying or we have an
        # error, then it is more important than any info we could get from the
        # flags so we show that
        if stage.probably_airborne or stage.has_error:
            return stage.description

        # Looks like we are on the ground, so show the info that we can gather
        # from the flags
        if not flags & DroneShowStatusFlag.HAS_ORIGIN:
            return "Origin not set"
        elif not flags & DroneShowStatusFlag.HAS_ORIENTATION:
            return "Orientation not set"
        elif not flags & DroneShowStatusFlag.HAS_START_TIME:
            return "Start time not set"
        elif not flags & DroneShowStatusFlag.HAS_GEOFENCE:
            return "Geofence not set"

        # We are on the ground but there's nothing important to report from the
        # flags so just show the description of the stage
        return stage.description
