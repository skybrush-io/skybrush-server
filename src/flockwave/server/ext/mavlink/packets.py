from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from enum import IntEnum, IntFlag
from functools import lru_cache
from struct import Struct, pack
from time import time
from typing import ClassVar, Optional, Sequence

from flockwave.gps.time import unix_to_gps_time_of_week, gps_time_of_week_to_utc
from flockwave.server.ext.show.config import AuthorizationScope
from flockwave.server.model.gps import GPSFixType as OurGPSFixType

from .enums import GPSFixType
from .types import MAVLinkMessage, MAVLinkMessageSpecification, spec

__all__ = ("DroneShowStatus", "create_led_control_packet")


_EMPTY = b"\x00" * 256
"""Helper constant used when we try to send an empty byte array via MAVLink."""

MSEC_IN_WEEK = 604800000
"""Number of milliseconds in a normal week."""


def authorization_scope_to_int(authorization_scope: AuthorizationScope) -> int:
    """Converts an authorization scope enum value to its integer representation
    in the Skybrush firmware.
    """
    if authorization_scope is AuthorizationScope.NONE:
        return 0
    elif authorization_scope is AuthorizationScope.LIVE:
        return 1
    elif authorization_scope is AuthorizationScope.REHEARSAL:
        return 2
    elif authorization_scope is AuthorizationScope.LIGHTS_ONLY:
        return 3
    else:
        return 0


def authorization_scope_from_int(value: int) -> AuthorizationScope:
    """Converts the integer representation of an authorization scope enum value
    to the enum itself.
    """
    if value == 1:
        return AuthorizationScope.LIVE
    elif value == 2:
        return AuthorizationScope.REHEARSAL
    elif value == 3:
        return AuthorizationScope.LIGHTS_ONLY
    else:
        return AuthorizationScope.NONE


def create_custom_data_packet(type: int, payload: bytes) -> MAVLinkMessageSpecification:
    """Creates a custom data packet used by our firmware with the given type
    and payload.
    """
    length = len(payload) + 1
    if length <= 16:
        padded_length = 16
        packet = spec.data16
    elif length <= 32:
        padded_length = 32
        packet = spec.data32
    elif length <= 64:
        padded_length = 64
        packet = spec.data64
    elif length <= 96:
        padded_length = 96
        packet = spec.data96
    else:
        raise ValueError("payload too long")

    return packet(
        type=0x5C,
        len=length,
        data=bytes([type]) + payload + b"\x00" * (padded_length - length),
    )


def create_start_time_configuration_packet(
    authorization_scope: AuthorizationScope,
    start_time: Optional[float] = None,
    should_update_takeoff_time: bool = True,
) -> MAVLinkMessageSpecification:
    """Creates a custom command packet used by our firmware that sets the
    scheduled takeoff time and the takeoff authorization of the swarm.

    Parameters:
        start_time: the desired takeoff time of the swarm as a UNIX timestamp;
            `None` if it should be cleared
        authorized: whether the swarm is authorized to take off
        should_update_takeoff_time: whether the desired takeoff time should be
            updated on the swarm; set this to `False` if you do not want to
            change the start time, only the authorization flag
    """
    if not should_update_takeoff_time:
        # do not touch; this is expressed by a value larger than 604800 seconds
        # on the drone's side.
        start_time = 0x7FFFFFFF
        msec_until_start = 0x7FFFFFFF
    elif start_time is None or start_time < 0:
        # clear start time; this is expressed by a value smaller than -604800
        # seconds on the drone's side
        start_time = -0x80000000
        msec_until_start = -0x80000000
    else:
        # convert from UNIX timestamp to GPS time-of-week
        msec_until_start = int(1000 * (start_time - time()))
        msec_until_start = min(max(msec_until_start, -MSEC_IN_WEEK), MSEC_IN_WEEK)
        _, start_time = unix_to_gps_time_of_week(int(start_time))

    scope_value = authorization_scope_to_int(authorization_scope)
    return create_custom_data_packet(
        type=1, payload=pack("<iBi", start_time, scope_value, msec_until_start)
    )


def create_led_control_packet(
    data: Optional[Sequence[int]] = None, broadcast: bool = False
) -> MAVLinkMessageSpecification:
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

    # Flags from now on come from the third flag byte in the status packet;
    # four most significant bits only.
    IS_FAR_FROM_EXPECTED_POSITION = 1 << 17

    # Flags from now on come from the second flag byte in the status packet;
    # four most significant bits only.
    IS_MISPLACED_BEFORE_TAKEOFF = 1 << 11
    UNUSED_1 = 1 << 10
    UNUSED_2 = 1 << 9
    UNUSED_3 = 1 << 8

    # Flags from now on come from the first flag byte in the status packet
    HAS_SHOW_FILE = 1 << 7
    HAS_START_TIME = 1 << 6
    HAS_ORIGIN = 1 << 5
    HAS_ORIENTATION = 1 << 4
    HAS_GEOFENCE = 1 << 3
    HAS_AUTHORIZATION_TO_START = 1 << 2
    IS_GPS_TIME_BAD = 1 << 1
    GEOFENCE_BREACHED = 1 << 0

    # Dummy member to be used as a default value
    OFF = 0


_stage_descriptions = {
    0: "",
    1: "Initializing...",
    2: "Countdown to show start",
    3: "Taking off",
    4: "Performing show",
    5: "Return to launch",
    6: "Position hold",
    7: "Landing",
    8: "Landed",
    9: "Error",
    10: "Running light program",
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
    TESTING_LIGHTS = 10

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
    """Dataclass representing a Skybrush-specific drone show status object.

    This dataclass encapsulates information that can be extracted from the
    standard drone show status packet. Information that can be extracted only
    from the extended status packet is provided in a separate object attached
    to the `extension` property.
    """

    start_time: int = -1
    """Scheduled start time of the drone show, in GPS seconds of week, negative
    if not set.
    """

    elapsed_time: int = 0
    """Number of seconds elapsed in the drone show."""

    flags: int = DroneShowStatusFlag.OFF.value
    """Various status flags."""

    stage: DroneShowExecutionStage = DroneShowExecutionStage.OFF
    """Stage of the drone show execution."""

    light: int = 0
    """Current color of the RGB light, in RGB565 encoding."""

    gps_fix: OurGPSFixType = OurGPSFixType.NO_GPS
    """Current GPS fix."""

    num_satellites: int = 0
    """Number of satellites seen."""

    authorization_scope: AuthorizationScope = AuthorizationScope.NONE
    """Authorization scope of the scheduled start of the drone show."""

    rtcm_counters: Sequence[Optional[int]] = (None, None)
    """Number of RTCM messages received from the primary and secondary channels
    in the last few seconds.
    """

    extension: Optional[DroneShowStatusExtension] = None
    """Extended status information, if provided at construction time."""

    TYPE: ClassVar[int] = 0x5B
    """Identifier of Skybrush-specific DATA* show status packets."""

    _struct: ClassVar[Struct] = Struct("<iHBBBBhBB")
    """Structure of Skybrush-specific DATA* show status packets."""

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatus_ object from the raw body of a MAVLink
        DATA* packet that has already been truncated to the desired length of
        the packet.
        """
        # Remember the original length so we can decide how much padding was
        # added later
        data_len = len(data)
        is_extended = data_len > 14

        # Length of standard packet is 14 bytes. Pad it to the required length
        # if the data is shorter than 14 bytes.
        if data_len < 14:
            data = data.ljust(14, b"\x00")

        # Unpack the standard section of the packet
        (
            start_time,
            light,
            flags,
            flags2,
            gps_health,
            flags3,
            elapsed_time,
            primary_rtcm_count_plus_one,
            secondary_rtcm_count_plus_one,
        ) = cls._struct.unpack(data[:14])

        # Merge flags, flags2 and flags3 into one byte. Lower 4 bits of flags2
        # is the execution stage. Lower 2 bits of flags3 is the boot count
        # modulo 4.
        flags |= (flags3 & 0xF0) << 10
        flags |= (flags2 & 0xF0) << 4
        stage = flags2 & 0x0F

        if data_len <= 9:
            # No "flags3" field in the original packet. "flags3" contains the
            # authorization scope and we need to keep it consistent with the
            # authorization flag in the "flags" field.
            if flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START.value:
                flags3 |= (0x01) << 2

        # validate the authorization scope
        scope = authorization_scope_from_int((flags3 & 0x0C) >> 2)

        # validate the execution stage
        try:
            stage = DroneShowExecutionStage(stage)
        except Exception:
            stage = DroneShowExecutionStage.UNKNOWN

        # process the RTCM counters
        rtcm_counters = (
            primary_rtcm_count_plus_one - 1
            if primary_rtcm_count_plus_one > 0
            else None,
            secondary_rtcm_count_plus_one - 1
            if secondary_rtcm_count_plus_one > 0
            else None,
        )

        # if the packet is an extended status packet, parse the extended bits
        extension = (
            DroneShowStatusExtension.from_bytes(data[14:]) if is_extended else None
        )

        return cls(
            start_time=start_time,
            elapsed_time=elapsed_time,
            light=light,
            flags=flags,
            stage=stage,
            gps_fix=GPSFixType.to_ours(gps_health & 0x07),
            num_satellites=gps_health >> 3,
            authorization_scope=scope,
            rtcm_counters=rtcm_counters,
            extension=extension,
        )

    @classmethod
    def from_mavlink_message(cls, message: MAVLinkMessage):
        """Constructs a DroneShowStatus_ object from a MAVLink DATA* packet.

        Raises:
            ValueError: if the type of the MAVLink DATA* packet does not match
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
        return bool(self.flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START.value)

    @property
    def has_timesync_error(self) -> bool:
        """Returns whether there is probably a time synchronization problem
        as we are receiving invalid timestamps from the GPS.
        """
        return bool(self.flags & DroneShowStatusFlag.IS_GPS_TIME_BAD.value)

    @property
    def is_far_from_expected_position(self) -> bool:
        """Returns whether the drone seems to be far from its expected position,
        typically during a show.
        """
        return bool(
            self.flags & DroneShowStatusFlag.IS_FAR_FROM_EXPECTED_POSITION.value
        )

    @property
    def is_geofence_breached(self) -> bool:
        """Returns whether at least one of the geofences is breached, even if
        the drone is configured not to act on them (i.e. report only)."""
        return bool(self.flags & DroneShowStatusFlag.GEOFENCE_BREACHED.value)

    @property
    def is_misplaced_before_takeoff(self) -> bool:
        """Returns whether we are currently before the takeoff stage and the
        drone seems to be misplaced.
        """
        return bool(self.flags & DroneShowStatusFlag.IS_MISPLACED_BEFORE_TAKEOFF.value)

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
        # This function is called frequently, and Python enums are a bit slow
        # so we optimize enum access by using the 'value' property on them

        flags = self.flags
        stage = self.stage

        if not flags & DroneShowStatusFlag.HAS_SHOW_FILE.value:
            return "No show data"

        # If we are in a stage that implies that we are flying or we have an
        # error, then it is more important than any info we could get from the
        # flags so we show that
        if stage.probably_airborne or stage.has_error:
            return stage.description

        # Looks like we are on the ground, so show the info that we can gather
        # from the flags
        if not flags & DroneShowStatusFlag.HAS_ORIGIN.value:
            return "Origin not set"
        elif not flags & DroneShowStatusFlag.HAS_ORIENTATION.value:
            return "Orientation not set"
        elif flags & DroneShowStatusFlag.IS_MISPLACED_BEFORE_TAKEOFF.value:
            return "Not at takeoff position"
        elif (
            not flags & DroneShowStatusFlag.HAS_START_TIME.value
            and stage != DroneShowExecutionStage.LANDED
        ):
            if flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START.value:
                return "Authorized without start time"
            elif self.gps_fix < OurGPSFixType.FIX_3D.value:
                # This is needed here to explain why the start time might not
                # have been set yet; interpreting the SHOW_START_TIME parameter
                # needs GPS fix
                return "No 3D GPS fix yet"
            elif flags & DroneShowStatusFlag.IS_GPS_TIME_BAD.value:
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
            not flags & DroneShowStatusFlag.HAS_AUTHORIZATION_TO_START.value
            and stage != DroneShowExecutionStage.LANDED
        ):
            if stage is DroneShowExecutionStage.OFF:
                # We are not even in show mode so the lack of authorization is not relevant
                return ""
            else:
                return "Not authorized to start"
        elif not flags & DroneShowStatusFlag.HAS_GEOFENCE.value:
            return "Geofence not set"
        elif self.gps_fix < OurGPSFixType.FIX_3D:
            return "No 3D GPS fix yet"

        # We are on the ground but there's nothing important to report from the
        # flags so just show the description of the stage
        return stage.description


@dataclass
class DroneShowStatusExtension:
    """Dataclass representing the extended part of a Skybrush-specific drone
    show status object.

    This dataclass encapsulates information that can be extracted from the
    extended drone show status packet only.
    """

    lat: float
    """Latitude of the drone in degrees."""

    lng: float
    """Longitude of the drone in degrees."""

    alt: float
    """Altitude of the drone in meters above sea level."""

    relative_alt: float
    """Altitude of the drone relative to its home altitude, in meters."""

    vx: float
    """X (North) coordinate of the velocity of the drone, in m/s."""

    vy: float
    """Y (East) coordinate of the velocity of the drone, in m/s."""

    vz: float
    """Z (Down) coordinate of the velocity of the drone, in m/s."""

    heading: float
    """Heading of the drone."""

    h_acc: Optional[float] = None
    """Horizontal accuracy as reported by the GPS; ``None`` if not provided."""

    v_acc: Optional[float] = None
    """Vertical accuracy as reported by the GPS; ``None`` if not provided."""

    show_id: Optional[int] = None
    """Unique identifier of the show uploaded to the drone, if known."""

    trajectory_index: Optional[int] = None
    """Index of the trajectory that the drone is going to fly, if known."""

    _struct: ClassVar[Struct] = Struct("<iiiihhhHHHIH")
    """Structure of the packet."""

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a DroneShowStatusExtension_ object from the appropriate
        section of the raw body of a MAVLink DATA* packet.

        Typically you should not need to call this method directly; call
        `DroneShowStatus.from_bytes(...)` on the full packet payload instead.
        """
        (
            lat,
            lng,
            alt,
            relative_alt,
            vx,
            vy,
            vz,
            heading,
            h_acc,
            v_acc,
            show_id,
            trajectory_index,
        ) = cls._struct.unpack(data[: cls._struct.size])

        if abs(lat) <= 900000000:
            # latitude, longitude: convert to degrees
            lat /= 1e7
            lng /= 1e7
            # altitudes: [mm] --> [m]
            alt /= 1e3
            relative_alt /= 1e3
        else:
            # treat invalid latitudes as an indication of "no GPS fix"
            lat = lng = alt = relative_alt = 0.0

        # velocity: [cm/s] --> [m/s]
        vx /= 100.0
        vy /= 100.0
        vz /= 100.0

        # heading: [cdeg] --> [deg]
        heading = heading / 100.0 if abs(heading) <= 36000 else 0.0

        # GPS accuracy
        h_acc = h_acc / 1000.0 if h_acc > 0 else None
        v_acc = v_acc / 1000.0 if v_acc > 0 else None

        # TODO(ntamas): show ID, trajectory index
        return cls(lat, lng, alt, relative_alt, vx, vy, vz, heading, h_acc, v_acc)
