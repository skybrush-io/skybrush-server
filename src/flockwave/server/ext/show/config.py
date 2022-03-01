"""Configuration object for the drone show extension."""

from __future__ import annotations

from blinker import Signal
from enum import Enum
from typing import Any, Dict, List, Optional, TypeVar

from flockwave.server.tasks.led_lights import LightConfiguration
from flockwave.server.utils import format_timestamp_nicely, format_uav_ids_nicely

__all__ = ("LightConfiguration", "DroneShowConfiguration", "StartMethod")


class StartMethod(Enum):
    """Enumeration holding the possible start methods for a drone show."""

    #: Show starts only with RC
    RC = "rc"

    #: Show starts automatically based on GPS time or MIDI timecode
    AUTO = "auto"

    def describe(self) -> str:
        """Returns a human-readable description of the start method."""
        return (
            "Show starts only with RC"
            if self is StartMethod.RC
            else "Show starts automatically based on a designated start time"
        )


C = TypeVar("C", bound="DroneShowConfiguration")


class DroneShowConfiguration:
    """Main configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    authorized_to_start: bool
    """Whether the show is authorized to start."""

    start_method: StartMethod
    """The start method of the show (RC or automatic with countdown)."""

    start_time: Optional[float]
    """The start time of the show; ``None`` if unscheduled."""

    uav_ids: List[Optional[str]]
    """The list of UAV IDs participating in the show."""

    def __init__(self):
        """Constructor."""
        self.authorized_to_start = False
        self.start_time = None
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

        if self.start_time is None:
            fmt_start_time = ""
        else:
            fmt_start_time = format_timestamp_nicely(self.start_time)
            fmt_start_time = f" at {fmt_start_time}"

        if uav_ids_relevant:
            uav_ids = [id for id in self.uav_ids or () if id is not None]
            uav_ids.sort()
            fmt_uav_count = format_uav_ids_nicely(uav_ids, max_items=3)
        else:
            fmt_uav_count = "UAVs"

        if self.authorized_to_start:
            return (
                f"{fmt_uav_count} authorized to start{fmt_start_method}{fmt_start_time}"
            )
        else:
            return f"{fmt_uav_count} to start{fmt_start_method}{fmt_start_time}, not authorized"

    @property
    def json(self) -> Dict[str, Any]:
        """Returns the JSON representation of the configuration object."""
        return {
            "start": {
                "authorized": bool(self.authorized_to_start),
                "time": self.start_time,
                "method": str(self.start_method.value),
                "uavIds": self.uav_ids,
            }
        }

    def update_from_json(self, obj: Dict[str, Any]) -> None:
        """Updates the configuration object from its JSON representation."""
        changed = False

        start_conditions = obj.get("start")
        if start_conditions:
            if "authorized" in start_conditions:
                # This is intentional; in order to be on the safe side, we only
                # accept True for authorization, not any other truthy value
                self.authorized_to_start = start_conditions["authorized"] is True
                changed = True

            if "time" in start_conditions:
                start_time = start_conditions["time"]
                if start_time is None:
                    self.start_time = None
                    changed = True
                elif isinstance(start_time, (int, float)):
                    self.start_time = float(start_time)
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

        if changed:
            self.updated.send(self)
