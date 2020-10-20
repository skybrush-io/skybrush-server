"""Temporary place for functions that are related to the processing of
Skybrush-related geofence specifications, until we find a better place for them.
"""

from typing import Dict

from flockwave.server.model.geofence import GeofenceConfigurationRequest
from flockwave.server.utils import optional_float

__all__ = ("get_geofence_configuration_from_show_specification",)


def get_geofence_configuration_from_show_specification(
    show: Dict,
) -> GeofenceConfigurationRequest:
    result = GeofenceConfigurationRequest()

    geofence = show.get("geofence", None)
    if not geofence:
        # Show contains no geofence specification so nothing to configure, just
        # leave the request empty
        return result

    version = geofence.get("version", 0)
    if version is None:
        raise RuntimeError("geofence specification must have a version number")
    if version != 1:
        raise RuntimeError("only version 1 geofences are supported")

    result.enabled = bool(geofence.get("enabled", True))
    result.max_altitude = optional_float(geofence.get("maxAltitude"))
    result.max_distance = optional_float(geofence.get("maxDistance"))
    result.min_altitude = optional_float(geofence.get("minAltitude"))

    # We need a minimum altitude anyway, so if we don't have one, let's assume
    # the ArduCopter default
    if result.min_altitude is None:
        result.min_altitude = -10

    # TODO(ntamas): parse polygons!

    return result
