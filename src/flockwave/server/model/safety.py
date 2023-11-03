"""safety-related data structures and functions for the server."""

from dataclasses import dataclass
from typing import Any, Optional

__all__ = ("SafetyConfigurationRequest",)


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

    low_battery_voltage: Optional[float] = None
    """Low battery voltage in [V] under which a low battery failsafe action is
    triggered. `None` means not to change the low battery voltage setting."""

    critical_battery_voltage: Optional[float] = None
    """Critically low battery voltage in [V] under which a critical battery
    failsafe action is triggered. `None` means not to change the critical
    battery voltage setting."""

    return_to_home_altitude: Optional[float] = None
    """Altitude in [mAHL] at which return to home operations are performed.
     `None` means not to change the return to home altitude setting."""

    return_to_home_speed: Optional[float] = None
    """Horizontal speed in [m/s] at which return to home operations are
    performed. `None` means not to change the return to home speed setting."""

    @property
    def json(self) -> dict[str, Any]:
        """Returns a JSON representation of the safety configuration."""
        return {
            "version": 1,
            "lowBatteryVoltage": None
            if self.low_battery_voltage is None
            else round(self.low_battery_voltage, ndigits=3),
            "criticalBatteryVoltage": None
            if self.critical_battery_voltage is None
            else round(self.critical_battery_voltage, ndigits=3),
            "returnToHomeAltitude": None
            if self.return_to_home_altitude is None
            else round(self.return_to_home_altitude, ndigits=3),
            "returnToHomeSpeed": None
            if self.return_to_home_speed is None
            else round(self.return_to_home_speed, ndigits=3),
        }
