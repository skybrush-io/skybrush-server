"""Skybrush server extension that adds support for drone flocks that use
the MAVLink protocol.
"""

from .extension import MAVLinkDronesExtension
from .schema import schema

__all__ = ("construct", "dependencies", "description", "enhancers", "schema")

construct = MAVLinkDronesExtension
dependencies = ("show", "signals")
description = "Support for drones that use the MAVLink protocol"
enhancers = {"firmware_update": MAVLinkDronesExtension.use_firmware_update_support}
