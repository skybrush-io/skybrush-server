"""Configuration object for the drone show extension."""

from __future__ import annotations

from blinker import Signal
from enum import Enum
from typing import Any, Optional, TypeVar

from flockwave.server.tasks.led_lights import LightConfiguration
from flockwave.server.utils.formatting import (
    format_timedelta_nicely,
    format_timestamp_nicely,
    format_uav_ids_nicely,
)

__all__ = ("LightConfiguration", "DroneShowConfiguration", "StartMethod")


class StartMethod(Enum):
    """Enumeration holding the possible start methods for a drone show."""

    RC = "rc"
    """Show starts only with RC."""

    AUTO = "auto"
    """Show starts automatically based on GPS time or MIDI timecode."""

    def describe(self) -> str:
        """Returns a human-readable description of the start method."""
        return (
            "Show starts only with RC"
            if self is StartMethod.RC
            else "Show starts automatically based on a designated start time"
        )


class AuthorizationScope(Enum):
    """Enumeration describing the possible scopes of an authorization."""

    NONE = "none"
    """No authorization. Typically this is not used as we have a separate
    boolean in the show configuration to indicate whether authorization has
    been granted or not so it makes no sense to grant authorization with this
    scope. Nevertheless, it is included for sake of completeness.
    """

    LIVE = "live"
    """Authorization to perform the full show in a live setting, with audience.
    All safety settings that are configured to be enabled will be enforced.
    """

    REHEARSAL = "rehearsal"
    """Authorization to perform a rehearsal of the show. Safety settings might
    be turned off or relaxed depending on the configuration of the drones.
    """

    LIGHTS_ONLY = "lights"
    """Authorization to perform the show with lights only. Drones will not
    turn on their motors and will not fly.
    """


C = TypeVar("C", bound="DroneShowConfiguration")


class DroneShowConfiguration:
    """Main configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    authorized_to_start: bool
    """Whether the show is authorized to start (subject to restrictions in
    ``authorization_scope``)."""

    authorization_scope: AuthorizationScope
    """Scope of the authorization for the show. ``AuthorizationScope.NONE``
    implies that `authorized_to_start` is ``False``, but the scope may also be
    another value if `authorized_to_start` is ``False`` (to make it easier to
    grant or revoke authorization without changing the scope).
    """

    duration: Optional[float]
    """Duration of the show, if known. ``None`` means unknown duration."""

    start_method: StartMethod
    """The start method of the show (RC or automatic with countdown)."""

    start_time_on_clock: Optional[float]
    """The start time of the show, in seconds elapsed since the epoch of the
    associated clock; ``None`` if unscheduled.
    """

    clock: Optional[str]
    """Identifier of the clock that the start time refers to. ``None`` means
    that the start time is a UNIX timestamp, otherwise it must refer to an
    existing clock in the system and the start time is interpreted as
    _seconds_ relative to the epoch of the existing clock.
    """

    uav_ids: list[Optional[str]]
    """The list of UAV IDs participating in the show."""

    def __init__(self):
        """Constructor."""
        self.authorized_to_start = False
        self.authorization_scope = AuthorizationScope.NONE
        self.clock = None
        self.duration = None
        self.start_time_on_clock = None
        self.start_method = StartMethod.RC
        self.uav_ids = []

    def clone(self: C) -> C:
        """Makes an exact shallow copy of the configuration object."""
        result = self.__class__()
        result.update_from_json(self.json)
        return result

    def format(self) -> str:
        """Formats the configuration object in a human-readable format for
        logging purposes.
        """
        if self.start_method is StartMethod.RC:
            fmt_start_method = " with RC"
            uav_ids_relevant = False
        elif self.start_method is StartMethod.AUTO:
            fmt_start_method = " automatically"
            uav_ids_relevant = True
        else:
            fmt_start_method = ""
            uav_ids_relevant = False

        if self.start_time_on_clock is None:
            fmt_start_time = ""
        else:
            if self.clock:
                # Clock is synchronized to some other internal clock
                fmt_start_time = format_timedelta_nicely(self.start_time_on_clock)
                fmt_start_time = f" at {fmt_start_time} on clock {self.clock!r}"
            else:
                fmt_start_time = format_timestamp_nicely(self.start_time_on_clock)
                fmt_start_time = f" at {fmt_start_time}"

        if uav_ids_relevant:
            uav_ids = [id for id in self.uav_ids or () if id is not None]
            uav_ids.sort()
            fmt_uav_count = format_uav_ids_nicely(uav_ids, max_items=3)
        else:
            fmt_uav_count = "UAVs"

        if self.authorized_to_start:
            if self.authorization_scope is AuthorizationScope.REHEARSAL:
                fmt_scope = " for rehearsal"
            elif self.authorization_scope is AuthorizationScope.LIGHTS_ONLY:
                fmt_scope = " with lights only"
            else:
                fmt_scope = ""

            return f"{fmt_uav_count} authorized to start{fmt_scope}{fmt_start_method}{fmt_start_time}"
        else:
            return f"{fmt_uav_count} to start{fmt_start_method}{fmt_start_time}, not authorized"

    @property
    def is_synced_to_custom_clock(self) -> bool:
        """Returns whether the show configuration specifies that the show start
        should be synced to a custom, internal clock.
        """
        return self.clock is not None and self.start_time_on_clock is not None

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of the configuration object."""
        result: dict[str, Any] = {
            "start": {
                "authorized": bool(self.authorized_to_start),
                "authorizationScope": self.authorization_scope.value,
                "clock": self.clock,
                "time": self.start_time_on_clock,
                "method": str(self.start_method.value),
                "uavIds": self.uav_ids,
            }
        }
        if self.duration is not None:
            result["duration"] = self.duration

        return result

    @property
    def scope_iff_authorized(self) -> AuthorizationScope:
        """Returns the authorization scope if the show is authorized to start,
        otherwise ``AuthorizationScope.NONE``.
        """
        return (
            self.authorization_scope
            if self.authorized_to_start
            else AuthorizationScope.NONE
        )

    def update_from_json(self, obj: dict[str, Any]) -> None:
        """Updates the configuration object from its JSON representation."""
        changed = False

        # Handle start conditions
        start_conditions = obj.get("start")
        if start_conditions:
            if "authorized" in start_conditions:
                # This is intentional; in order to be on the safe side, we only
                # accept True for authorization, not any other truthy value
                self.authorized_to_start = start_conditions["authorized"] is True

                # For sake of compatibility with versions that did not have an
                # authorizationScope member, force the scope to be LIVE if it
                # was NONE.
                if (
                    self.authorized_to_start
                    and self.authorization_scope is AuthorizationScope.NONE
                ):
                    self.authorization_scope = AuthorizationScope.LIVE

                changed = True

            if "authorizationScope" in start_conditions:
                authorization_scope = start_conditions["authorizationScope"]
                if isinstance(authorization_scope, str):
                    try:
                        self.authorization_scope = AuthorizationScope(
                            authorization_scope
                        )
                        changed = True
                    except ValueError:
                        pass

            if "time" in start_conditions:
                start_time = start_conditions["time"]
                if start_time is None:
                    self.start_time_on_clock = None
                    changed = True
                elif isinstance(start_time, (int, float)):
                    self.start_time_on_clock = float(start_time)
                    changed = True

            if "method" in start_conditions:
                self.start_method = StartMethod(start_conditions["method"])
                changed = True

            if "uavIds" in start_conditions:
                uav_ids = start_conditions["uavIds"]
                if isinstance(uav_ids, list) and all(
                    item is None or isinstance(item, str) for item in uav_ids
                ):
                    self.uav_ids = uav_ids
                    changed = True

            if "clock" in start_conditions:
                clock = start_conditions["clock"]
                if clock is None or isinstance(clock, str):
                    # Make sure that an empty string is mapped to None
                    self.clock = clock if clock else None
                    changed = True

        # Handle duration
        if "duration" in obj:
            if obj["duration"] is None:
                self.duration = None
                changed = True
            elif isinstance(obj["duration"], (int, float)) and obj["duration"] >= 0:
                self.duration = float(obj["duration"])
                changed = True

        # Enforce consistency of authorized_to_start and authorization_scope
        if self.authorized_to_start:
            if self.authorization_scope is AuthorizationScope.NONE:
                self.authorized_to_start = False
                changed = True

        if changed:
            self.updated.send(self)
