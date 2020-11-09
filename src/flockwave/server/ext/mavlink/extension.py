"""Skybrush server extension that adds support for drone flocks using the
MAVLink protocol.
"""

from __future__ import absolute_import

from contextlib import ExitStack
from functools import partial
from typing import Optional, Sequence, Tuple

from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model.uav import UAV
from flockwave.server.utils import optional_int, overridden

from .driver import MAVLinkDriver
from .network import MAVLinkNetwork
from .tasks import check_uavs_alive
from .types import (
    MAVLinkMessage,
    MAVLinkMessageMatcher,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
)


__all__ = ("construct", "dependencies")


#: Dictionary that resolves common connection preset aliases used in
#: the configuration file
CONNECTION_PRESETS = {
    "default": "udp://:14550?broadcast_port=14555",
    "apm-sitl": "tcp://localhost:5760",
    "px4-sitl": "tcp://localhost:4560",
}


class MAVLinkDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the MAVLink
    protocol.
    """

    def __init__(self):
        super(MAVLinkDronesExtension, self).__init__()

        self._driver = None
        self._networks = None
        self._start_method = None
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
        driver.log = self.log
        driver.mandatory_custom_mode = optional_int(configuration.get("custom_mode"))
        driver.run_in_background = self.run_in_background
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

        # Get a handle to the signals that we will need
        signals = app.import_api("signals")
        rtk_signal = signals.get("rtk:packet")
        show_start_method_changed_signal = signals.get("show:config_updated")

        # Create self._uavs only here and not in the constructor; this is to
        # ensure that we cannot accidentally register a UAV when the extension
        # is not running yet
        uavs = []

        # Create a cleanup context and run the extension
        with ExitStack() as stack:
            stack.enter_context(overridden(self, _uavs=uavs, _networks=networks))

            # Connect the signals to our signal handlers
            stack.enter_context(rtk_signal.connected_to(self._on_rtk_correction_packet))
            stack.enter_context(
                show_start_method_changed_signal.connected_to(
                    self._on_start_method_changed
                )
            )

            # Get the current start configuration for the drones in this network.
            # Note that this can be called only if self._networks has been set
            # up so we cannot do it outside the exit stack
            self._update_start_method_in_networks(app=app)

            try:
                async with self.use_nursery() as nursery:
                    # Create one task for each network
                    for network in networks.values():
                        nursery.start_soon(partial(network.run, **kwds))

                    # Create an additional task that periodically checks whether the UAVs
                    # registered in the extension are still alive
                    nursery.start_soon(check_uavs_alive, uavs)
            finally:
                for uav in uavs:
                    app.object_registry.remove(uav)

    def _get_network_specifications_from_configuration(self, configuration):
        # Construct the network specifications first
        if "networks" in configuration:
            if "connections" in configuration:
                self.log.warn(
                    "Move the 'connections' configuration key inside a network; "
                    + "'connections' ignored when 'networks' is present"
                )
            network_specs = configuration["networks"]
        else:
            network_specs = {
                "mav": {"connections": configuration.get("connections", ())}
            }

        # Determine the default ID format from the configuration
        default_id_format = configuration.get("id_format", None)
        if not default_id_format:
            # Add the network ID in front of the system ID if we have multiple
            # networks, otherwise just use the system ID
            default_id_format = "{1}:{0}" if len(network_specs) > 1 else "{0}"

        # Create the object holding the defaults for the individual network
        # configurations
        MISSING = object()
        network_spec_defaults = {
            "id_format": default_id_format,
            "packet_loss": configuration.get("packet_loss", MISSING),
            "system_id": configuration.get("system_id", 255),
        }

        # Apply the default ID format for networks that do not specify an
        # ID format on their own
        for spec in network_specs.values():
            for key, value in network_spec_defaults.items():
                if key not in spec and value is not MISSING:
                    spec[key] = value

            # Resolve common connection aliases
            if "connections" in spec:
                spec["connections"] = [
                    CONNECTION_PRESETS.get(value, value)
                    for value in spec["connections"]
                ]
            print(repr(spec))

        # Return the network specifications
        return {
            key: MAVLinkNetworkSpecification.from_json(value, id=key)
            for key, value in network_specs.items()
        }

    def _on_rtk_correction_packet(self, sender, packet: bytes):
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in this network.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                this network
        """
        if not self._networks:
            return

        for name, network in self._networks.items():
            try:
                network.enqueue_rtk_correction_packet(packet)
            except Exception:
                self.log.warn(
                    f"Failed to enqueue RTK correction packet to network {name!r}"
                )

    def _on_start_method_changed(self, sender, config) -> None:
        """Handler that is called when the user changes the start time or start
        method of the drones in the `show` extension.
        """
        if not self._networks:
            return

        # Make a copy of the configuration in case someone else who comes after
        # us in the handler chain messes with it
        config = config.clone()

        # Send the configuration to all the networks
        self._update_start_method_in_networks(config)

    def _register_uav(self, uav: UAV) -> None:
        """Registers a new UAV object in the object registry of the application
        in a manner that ensures that the UAV is unregistered when the extension
        is stopped.
        """
        if self._uavs is None:
            raise RuntimeError("cannot register a UAV before the extension is started")

        self.app.object_registry.add(uav)
        self._uavs.append(uav)

    async def _send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: UAV,
        wait_for_response: Optional[MAVLinkMessageSpecification] = None,
        wait_for_one_of: Optional[Sequence[Tuple[str, MAVLinkMessageMatcher]]] = None,
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
                this argument to accept it as a response. The source system of
                the MAVLink message must also be equal to the system ID of the
                UAV where this message was sent.
        """
        network_id = target.network_id
        if not self._networks:
            raise RuntimeError("Cannot send packet; extension is not running")

        network = self._networks[network_id]
        return await network.send_packet(
            spec, target, wait_for_response, wait_for_one_of
        )

    def _update_start_method_in_networks(self, config=None, app=None) -> None:
        """Updates the start method of the drones managed by this extension,
        based on the given configuration object from the `show` extension. If
        the configuration object is `None`, retrieves it from the `show`
        extension itself.
        """
        if config is None:
            if app is None:
                raise RuntimeError(
                    "'app' kwarg must be provided if 'config' is missing"
                )

            config = app.import_api("show").get_configuration()

        for name, network in self._networks.items():
            try:
                network.notify_scheduled_takeoff_config_changed(config)
            except Exception:
                self.log.warn(
                    f"Failed to update start configuration of drones in network {name!r}"
                )


construct = MAVLinkDronesExtension
dependencies = ("show", "signals")
