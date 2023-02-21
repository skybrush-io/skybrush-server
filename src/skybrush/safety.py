"""Temporary place for functions that are related to the processing of
Skybrush-related safety specifications, until we find a better place for them.
"""

from typing import Dict

from flockwave.server.model.safety import SafetyConfigurationRequest
from flockwave.server.utils import optional_float

__all__ = ("get_safety_configuration_from_show_specification",)


def get_safety_configuration_from_show_specification(
    show: Dict,
) -> SafetyConfigurationRequest:
    result = SafetyConfigurationRequest()

    safety = show.get("safety", None)
    if not safety:
        # Show contains no safety specification so nothing to configure, just
        # leave the request empty
        return result

    version = safety.get("version", 0)
    if version is None:
        raise RuntimeError("safety specification must have a version number")
    if version != 1:
        raise RuntimeError("only version 1 safety specifications are supported")

    result.low_battery_voltage = optional_float(safety.get("lowBatteryVoltage"))
    result.critical_battery_voltage = optional_float(
        safety.get("criticalBatteryVoltage")
    )
    result.return_to_home_altitude = optional_float(safety.get("returnToHomeAltitude"))
    result.return_to_home_speed = optional_float(safety.get("returnToHomeSpeed"))

    return result
