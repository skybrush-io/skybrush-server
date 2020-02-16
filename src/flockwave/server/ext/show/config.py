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
