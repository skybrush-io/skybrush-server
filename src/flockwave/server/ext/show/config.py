"""Configuration object for the drone show extension."""

from blinker import Signal
from enum import Enum
from typing import List, Optional

__all__ = ("DroneShowConfiguration", "StartMethod")


class StartMethod(Enum):
    """Enumeration holding the possible start methods for a drone show."""

    RC = "rc"
    AUTO = "auto"


class DroneShowConfiguration:
    """Configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    def __init__(self):
        """Constructor."""
        self.authorized_to_start = False  # type: bool
        self.start_time = None  # type: Optional[float]
        self.start_method = StartMethod.RC  # type: StartMethod
        self.uav_ids = []  # type: List[Optional[str]]

    @property
    def json(self):
        """Returns the JSON representation of the configuration object."""
        return {
            "start": {
                "authorized": bool(self.authorized_to_start),
                "time": self.start_time,
                "method": str(self.start_method.value),
                "uavIds": self.uav_ids,
            }
        }

    def update_from_json(self, obj):
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
