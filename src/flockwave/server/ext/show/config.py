"""Configuration object for the drone show extension."""

from enum import Enum
from typing import Optional

__all__ = ("DroneShowConfiguration", "StartMethod")


class StartMethod(Enum):
    """Enumeration holding the possible start methods for a drone show."""

    RC = "rc"
    AUTO = "auto"


class DroneShowConfiguration:
    """Configuration object for the drone show extension."""

    def __init__(self):
        """Constructor."""
        self.start_time = None  # type: Optional[float]
        self.start_method = StartMethod.RC  # type: StartMethod

    @property
    def json(self):
        """Returns the JSON representation of the configuration object."""
        return {
            "start": {"time": self.start_time, "method": str(self.start_method.value)}
        }

    def update_from_json(self, obj):
        """Updates the configuration object from its JSON representation."""
        start_conditions = obj.get("start")
        if start_conditions:
            if "time" in start_conditions:
                start_time = start_conditions["time"]
                if start_time is None:
                    self.start_time = None
                elif isinstance(start_time, (int, float)):
                    self.start_time = float(start_time)

            if "method" in start_conditions:
                self.start_method = StartMethod(start_conditions["method"])
