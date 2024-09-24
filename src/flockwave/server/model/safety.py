"""safety-related data structures and functions for the server."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

__all__ = ("SafetyConfigurationRequest",)


class LowBatteryThresholdType(Enum):
    """Low battery threshold types."""

    OFF = "off"
    """Low battery threshold checking is switched off."""

    VOLTAGE = "voltage"
    """Low battery threshold is defined as a voltage value in [V]."""

    PERCENTAGE = "percentage"
    """Low battery threshold is defined as a percentage value in [%]."""


@dataclass
class LowBatteryThreshold:
    """Object representing low battery threshold settings."""

    type: LowBatteryThresholdType = LowBatteryThresholdType.OFF
    """The low battery threshold type."""

    value: float = 0.0
    """The low battery threshold value, defined in [V] or [%],
    depending on the low battery threshold type."""

    @classmethod
    def from_json(cls, obj: Any):
        """Constructs a low battery threshold configuration from its
        JSON representation.
        """
        return cls(
            type=LowBatteryThresholdType(obj.get("type")),
            value=obj.get("value"),
        )

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of the low battery threshold configuration."""
        return {"type": self.type.value, "value": round(self.value, ndigits=3)}


@dataclass
class SafetyConfigurationRequest:
    """Object representing a safety configuration object that can be enforced
    on a drone.

    This is admittedly minimal for the time being. We can update it as we
    implement support for more complex safety features. Things that are missing:

    - all kinds of failsafe settings
    - detailed proximity sensing setup (usage, distance from objects etc.)
    - threshold deviation from waypoint line that triggers an error
    - etc.

    Note that geofence-related safety settings are handled separately through a
    GeofenceConfigurationRequest object.

    """

    low_battery_threshold: Optional[LowBatteryThreshold] = None
    """Low battery threshold settings, defining a voltage or percentage value
    under which a low battery failsafe action is triggered, or explicitely
    disabling the low battery failsafe settings. `None` means not to change
    the low battery voltage setting."""

    critical_battery_voltage: Optional[float] = None
    """Critically low battery voltage in [V] under which a critical battery
    failsafe action is triggered. `None` means not to change the critical
    battery voltage setting."""

    return_to_home_altitude: Optional[float] = None
    """Minimum altitude in [mAHL] above which return to home operations are
    performed. `None` means not to change the return to home altitude setting."""

    return_to_home_speed: Optional[float] = None
    """Horizontal speed in [m/s] at which return to home operations are
    performed. `None` means not to change the return to home speed setting."""

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of the safety configuration."""
        return {
            "version": 2,
            "lowBatteryThreshold": (
                None
                if self.low_battery_threshold is None
                else self.low_battery_threshold.json
            ),
            "criticalBatteryVoltage": (
                None
                if self.critical_battery_voltage is None
                else round(self.critical_battery_voltage, ndigits=3)
            ),
            "returnToHomeAltitude": (
                None
                if self.return_to_home_altitude is None
                else round(self.return_to_home_altitude, ndigits=3)
            ),
            "returnToHomeSpeed": (
                None
                if self.return_to_home_speed is None
                else round(self.return_to_home_speed, ndigits=3)
            ),
        }
