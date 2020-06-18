"""Flockwave server extension that adds support for drone flocks using the
MAVLink protocol.
"""

from __future__ import absolute_import

from functools import partial
from trio import open_nursery
from typing import Optional

from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model.uav import UAV
from flockwave.server.utils import overridden

from .driver import MAVLinkDriver
from .network import MAVLinkNetwork
from .tasks import check_uavs_alive
from .types import (
    MAVLinkMessage,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
)


__all__ = ("construct", "dependencies")


class MAVLinkDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the MAVLink
    protocol.
    """

    def __init__(self):
        super(MAVLinkDronesExtension, self).__init__()
        self._driver = None
        self._networks = None
        self._nursery = None
        self._uavs = None

    def _create_driver(self):
        return MAVLinkDriver()

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
        driver.create_device_tree_mutator = self.create_device_tree_mutation_context
        driver.log = self.log.getChild("driver")
        driver.run_in_background = self._run_in_background
        driver.send_packet = self._send_packet

    async def run(self, app, configuration):
        networks = {
            network_id: MAVLinkNetwork.from_specification(spec)
            for network_id, spec in self._get_network_specifications_from_configuration(
                configuration
            ).items()
        }

        kwds = {
            "driver": self._driver,
            "log": self.log,
            "register_uav": self._register_uav,
            "supervisor": app.supervise,
            "use_connection": app.connection_registry.use,
        }

        # Create self._uavs only here and not in the constructor; this is to
        # ensure that we cannot accidentally register a UAV when the extension
        # is not running yet
        uavs = []
        with overridden(self, _uavs=uavs, _networks=networks):
            try:
                async with open_nursery() as nursery:
                    self._nursery = nursery

                    # Create one task for each network
                    for network in networks.values():
                        nursery.start_soon(partial(network.run, **kwds))

                    # Create an additional task that periodically checks whether the UAVs
                    # registered in the extension are still alive
                    nursery.start_soon(check_uavs_alive, uavs)
            finally:
                self._nursery = None

                for uav in uavs:
                    app.object_registry.remove(uav)

    def _get_network_specifications_from_configuration(self, configuration):
        if "networks" in configuration:
            if "connections" in configuration:
                self.log.warn(
                    "Move the 'connections' configuration key inside a network; "
                    + "'connections' ignored when 'networks' is present"
                )
            network_specs = configuration["networks"]
        else:
            network_specs = {"": {"connections": configuration.get("connections", ())}}

        return {
            key: MAVLinkNetworkSpecification.from_json(value, id=key)
            for key, value in network_specs.items()
        }

    def _register_uav(self, uav: UAV) -> None:
        """Registers a new UAV object in the object registry of the application
        in a manner that ensures that the UAV is unregistered when the extension
        is stopped.
        """
        if self._uavs is None:
            raise RuntimeError("cannot register a UAV before the extension is started")

        self.app.object_registry.add(uav)
        self._uavs.append(uav)

    def _run_in_background(self, func, *args) -> None:
        """Schedules the given async function to be executed as a background
        task in the nursery of the extension.

        The task will be cancelled if the extension is unloaded.
        """
        if self._nursery:
            self._nursery.start_soon(self._run_protected, func, *args)
        else:
            raise RuntimeError(
                "cannot run task in background, extension is not running"
            )

    async def _run_protected(self, func, *args) -> None:
        """Runs the given function in a "protected" mode that prevents exceptions
        emitted from it to crash the nursery that the function is being executed
        in.
        """
        try:
            await func(*args)
        except Exception:
            self.log.exception(
                f"Unexpected exception caught from background task {func.__name__}"
            )

    async def _send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: UAV,
        wait_for_response: Optional[MAVLinkMessageSpecification] = None,
    ) -> Optional[MAVLinkMessage]:
        """Sends a message to the given UAV and optionally waits for a matching
        response.

        Parameters:
            spec: the specification of the MAVLink message to send
            target: the UAV to send the message to
            wait_for_response: when not `None`, specifies a MAVLink message to
                wait for as a response. The message specification will be
                matched with all incoming MAVLink messages that have the same
                type as the type in the specification; all parameters of the
                incoming message must be equal to the template specified in
                this argument to accept it as a response.
        """
        network_id = target.network_id
        if not self._networks:
            raise RuntimeError("Cannot send packet; extension is not running")

        network = self._networks[network_id]
        return await network.send_packet(spec, target, wait_for_response)


construct = MAVLinkDronesExtension
dependencies = ()
