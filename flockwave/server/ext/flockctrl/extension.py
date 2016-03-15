"""Flockwave server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from flockwave.server.connections import create_connection
from flockwave.server.ext.base import ExtensionBase
from flockwave.server.model import ConnectionPurpose

__all__ = ("construct", )


class FlockCtrlDronesExtension(ExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def configure(self, configuration):
        conn = create_connection(configuration.get("connection"))
        self.app.connection_registry.add(
            conn, "XBee",
            description="Upstream XBee connection for FlockCtrl-based drones",
            purpose=ConnectionPurpose.uavRadioLink
        )


construct = FlockCtrlDronesExtension
