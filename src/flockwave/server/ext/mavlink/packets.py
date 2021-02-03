from dataclasses import dataclass
from datetime import timezone
from enum import IntEnum, IntFlag
from functools import lru_cache
from struct import Struct
from typing import Optional, Sequence

from flockwave.gps.time import gps_time_of_week_to_utc
from flockwave.server.model.gps import GPSFixType as OurGPSFixType

from .enums import GPSFixType
from .types import MAVLinkMessage, spec

__all__ = ("DroneShowStatus", "create_led_control_packet")


#: Helper constant used when we try to send an empty byte array via MAVLink
_EMPTY = b"\x00" * 256


def create_led_control_packet(
    data: Optional[Sequence[int]] = None, broadcast: bool = False
):
    """Creates a special LED light control packet used by our firmware."""
    kwds = {
        "instance": 42,
        "pattern": 42,
        "custom_len": len(data) if data else 0,
        "custom_bytes": bytes(data) + _EMPTY[len(data) :] if data else _EMPTY,
    }
    if broadcast:
        kwds.update(target_system=0, target_component=0)
    return spec.led_control(**kwds)


def format_elapsed_time(value: int) -> str:
    """Formats an elapsed time value in seconds into hour-minute-seconds
    format.
    """
    if value < 0:
        sign = "-"
        value = -value
    else:
        sign = " "

    minutes, seconds = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02}:{minutes:02}:{seconds:02}"


@lru_cache(maxsize=128)
def format_gps_time_of_week(value: int) -> str:
    # TODO(ntamas): the cache should be invalidated every week, but at the
    # moment it is unlikely that the server keeps on running for more than a
    # week without interruptions, so let's just ignore this for the time being.
    # If we really need to, we can call format_gps_time_of_week.cache_clear()
    # regularly.
    if value < 0:
        return "---:--:--"
    else:
        dt = gps_time_of_week_to_utc(value)
        assert dt.tzinfo is timezone.utc
        return dt.astimezone(tz=None).strftime("@%H:%M:%S")


class DroneShowStatusFlag(IntFlag):
    """Status flags used in the Skybrush-specific drone show status packet."""

    HAS_SHOW_FILE = 1 << 7
    HAS_START_TIME = 1 << 6
    HAS_ORIGIN = 1 << 5
    HAS_ORIENTATION = 1 << 4
    HAS_GEOFENCE = 1 << 3
    HAS_AUTHORIZATION_TO_START = 1 << 2
    IS_GPS_TIME_BAD = 1 << 1


_stage_descriptions = {
    0: "",
    1: "Initializing...",
    2: "Ready to start",
    3: "Taking off",
    4: "Performing show",
    5: "Return to launch",
    6: "Position hold",
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
        description = (
            description if description is not None else f"Unknown stage {self}"
        )
        return description


@dataclass
class DroneShowStatus:
    """Data class representing a Skybrush-specific drone show status object."""

    #: Scheduled start time of the drone show, in GPS seconds of week, negative
    #: if not set
    start_time: int = -1

    #: Number of seconds elapsed in the drone show
    elapsed_time: int = 0

    #: Various status flags
    flags: DroneShowStatusFlag = 0

    #: Stage of the drone show execution
    stage: DroneShowExecutionStage = DroneShowExecutionStage.OFF

    #: Current color of the RGB light, in RGB565 encoding
    light: int = 0

    #: Current GPS fix
    gps_fix: GPSFixType = OurGPSFixType.NO_GPS

    #: Number of satellites seen
    num_satellites: int = 0

    #: Identifier of Skybrush-specific DATA16 show status packets
    TYPE = 0x5B

    #: Structure of Skybrush-specific DATA16 show status packets
    _struct = Struct("<iHBBBxh")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw body of a MAVLink
        DATA16 packet that has already been truncated to the desired length of
        the packet.
        """
        if len(data) < 12:
            data = data.ljust(12, b"\x00")

        start_time, light, flags, stage, gps_health, elapsed_time = cls._struct.unpack(
            data[:12]
        )

        try:
            stage = DroneShowExecutionStage(stage)
        except Exception:
            stage = DroneShowExecutionStage.UNKNOWN

        return cls(
            start_time=start_time,
            elapsed_time=elapsed_time,
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
    def has_start_time(self) -> bool:
        """Returns whether there is a valid start time in the drone show status
        message.
        """
        return self.start_time >= 0

    @property
    def has_takeoff_authorization(self) -> bool:
        """Returns whether the takeoff authorization flag is set in the drone
        show status message.
        """
        return bool(self.flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START)

    @property
    def message(self) -> str:
        """Returns a short status message string that can be used for reporting
        the status of the drone show subsystem on the UI.
        """
        if self.stage.probably_airborne or self.elapsed_time >= -30:
            clock = format_elapsed_time(self.elapsed_time)
        else:
            clock = format_gps_time_of_week(self.start_time)
        message = self._format_message()
        return f"[{clock}] {message}"

    def _format_message(self) -> str:
        """Formats a status message from the execution stage and flags found in
        the drone show status packet.
        """
        flags = self.flags
        stage = self.stage

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
        elif (
            not flags & DroneShowStatusFlag.HAS_START_TIME
            and stage != DroneShowExecutionStage.LANDED
        ):
            if flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START:
                return "Authorized without start time"
            elif self.gps_fix < OurGPSFixType.FIX_3D:
                # This is needed here to explain why the start time might not
                # have been set yet; interpreting the SHOW_START_TIME parameter
                # needs GPS fix
                return "No 3D GPS fix yet"
            elif flags & DroneShowStatusFlag.IS_GPS_TIME_BAD:
                # If we get here, it means that we _do_ have 3D fix _but_ we
                # still don't have a GPS timestamp. This can happen only if the
                # GPS is not sending us the full timestamp; e.g., if it sends
                # the iTOW but not the GPS week number (as it is on Entron 300
                # drones)
                return "Invalid GPS timestamp"
            elif stage is DroneShowExecutionStage.OFF:
                # We are not even in show mode so the start time info is not relevant
                return ""
            else:
                return "Start time not set"
        elif (
            not flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START
            and stage != DroneShowExecutionStage.LANDED
        ):
            if stage is DroneShowExecutionStage.OFF:
                # We are not even in show mode so the lack of authorization is not relevant
                return ""
            else:
                return "Not authorized to start"
        elif not flags & DroneShowStatusFlag.HAS_GEOFENCE:
            return "Geofence not set"
        elif self.gps_fix < OurGPSFixType.FIX_3D:
            return "No 3D GPS fix yet"

        # We are on the ground but there's nothing important to report from the
        # flags so just show the description of the stage
        return stage.description
