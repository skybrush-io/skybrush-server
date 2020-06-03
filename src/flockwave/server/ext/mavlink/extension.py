"""Flockwave server extension that adds support for drone flocks using the
MAVLink protocol.
"""

from __future__ import absolute_import

from flockwave.server.ext.base import UAVExtensionBase

from .comm import CommunicationManager
from .driver import MAVLinkDriver

__all__ = ("construct", "dependencies")


class MAVLinkDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the MAVLink
    protocol.
    """

    def __init__(self):
        super(MAVLinkDronesExtension, self).__init__()
        self._driver = None
        self._links = {}

    def _create_driver(self):
        return MAVLinkDriver()

    def _create_communication_links(self, configuration):
        """Creates the communication manager objects corresponding to the
        various MAVLink streams used by this extension.

        Parameters:
            configuration (dict): the configuration dictionary of the
                extension
        """
        connection_config = configuration.get("connections", {})

        for name, spec in connection_config.items():
            # TODO(ntamas): use spec
            self._links[name] = CommunicationManager(self, name)

    def configure_driver(self, driver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver (UAVDriver): the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        driver.id_format = configuration.get("id_format", "{0:02}")
        driver.log = self.log.getChild("driver")
        driver.create_device_tree_mutator = self.create_device_tree_mutation_context

        self._create_communication_links(configuration)
        # driver.send_packet = self.send_packet


construct = MAVLinkDronesExtension
dependencies = ()
