"""Skybrush server extension that adds support for drone flocks using the
MAVLink protocol.
"""

from contextlib import ExitStack
from functools import partial
from logging import Logger
from typing import Dict, Optional, TYPE_CHECKING

from flockwave.server.ext.base import UAVExtension
from flockwave.server.model.uav import UAV
from flockwave.server.registries.errors import RegistryFull
from flockwave.server.utils import optional_int, overridden

from .driver import MAVLinkDriver, MAVLinkUAV
from .network import MAVLinkNetwork
from .tasks import check_uavs_alive
from .types import (
    MAVLinkMessage,
    MAVLinkMessageMatcher,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
)

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

__all__ = ("construct", "dependencies")


#: Dictionary that resolves common connection preset aliases used in
#: the configuration file
CONNECTION_PRESETS = {
    "default": "udp-listen://:14550?broadcast_port=14555",
    "apm-sitl": "tcp://localhost:5760",
}

#: Default routing configuration for networks
DEFAULT_ROUTING = {"rtk": 0}


class MAVLinkDronesExtension(UAVExtension):
    """Extension that adds support for drone flocks using the MAVLink
    protocol.
    """

    app: "SkybrushServer"
    log: Logger

    _networks: Dict[str, MAVLinkNetwork]

    def __init__(self):
        super().__init__()

        self._driver = None
        self._networks = {}
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
        driver.broadcast_packet = self._broadcast_packet
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

        # Get a handle to the signals extension that we will need
        signals = app.import_api("signals")
        status_summary_signal = signals.get("mavlink:status_summary")
        rtk_packet_fragments_signal = signals.get("mavlink:rtk_fragments")

        # Create self._uavs only here and not in the constructor; this is to
        # ensure that we cannot accidentally register a UAV when the extension
        # is not running yet
        uavs = []

        # Create a list of arguments to pass to `network.run()` for each MAVLink
        # network that the extension manages
        kwds = {
            "driver": self._driver,
            "log": self.log,
            "register_uav": self._register_uav,
            "rtk_packet_fragments_signal": rtk_packet_fragments_signal,
            "supervisor": app.supervise,
            "use_connection": app.connection_registry.use,
        }

        # Create a cleanup context and run the extension
        with ExitStack() as stack:
            stack.enter_context(overridden(self, _uavs=uavs, _networks=networks))

            # Connect the signals to our signal handlers
            stack.enter_context(
                signals.use(
                    {
                        "rtk:packet": self._on_rtk_correction_packet,
                        "show:config_updated": self._on_show_configuration_changed,
                        "show:lights_updated": self._on_show_light_configuration_changed,
                    }
                )
            )

            # Forward the current start configuration for the drones in this network.
            # Note that this can be called only if self._networks has been set
            # up so we cannot do it outside the exit stack
            self._update_show_configuration_in_networks(app=app)

            # Also forward the current lights configuration for the drones in
            # this network.
            self._update_show_light_configuration_in_networks(app=app)

            try:
                async with self.use_nursery() as nursery:
                    # Create one task for each network
                    for network in networks.values():
                        nursery.start_soon(partial(network.run, **kwds))

                    # Create an additional task that periodically checks whether the UAVs
                    # registered in the extension are still alive, and that sends
                    # status summary signals to interested consumers (typically
                    # the sidekick extension)
                    nursery.start_soon(
                        check_uavs_alive, uavs, status_summary_signal, self.log
                    )
            finally:
                for uav in uavs:
                    app.object_registry.remove(uav)

    async def _broadcast_packet(
        self,
        spec: MAVLinkMessageSpecification,
        channel: Optional[str] = None,
    ):
        """Broadcasts a message to all the UAVs on all the networks managed by
        this extension.

        Parameters:
            spec: the specification of the MAVLink message to send
            channel: specifies the channel that the packet should be sent on;
                defaults to the preferred channel of the network
        """
        if not self._networks:
            raise RuntimeError("Cannot send packet; extension is not running")

        for network in self._networks.values():
            await network.broadcast_packet(spec, channel)

    def _get_network_specifications_from_configuration(
        self, configuration
    ) -> Dict[str, MAVLinkNetworkSpecification]:
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
            "routing": configuration.get("routing", DEFAULT_ROUTING),
            "statustext_targets": configuration.get(
                "statustext_targets", frozenset({"client", "server"})
            ),
            "system_id": configuration.get("system_id", 254),
        }

        # Apply the default ID format for networks that do not specify an
        # ID format on their own

        for spec in network_specs.values():
            for key, value in network_spec_defaults.items():
                if key not in spec and value is not MISSING:
                    # Clone value if it is mutable as we don't want to have
                    # any cross-play between different networks if they start
                    # modifying their configuration
                    if isinstance(value, list):
                        value = list(value)
                    elif isinstance(value, dict):
                        value = dict(value)
                    spec[key] = value

            # Resolve common connection aliases
            if "connections" in spec:
                spec["connections"] = [
                    CONNECTION_PRESETS.get(value, value)
                    for value in spec["connections"]
                ]

        # Return the network specifications
        return {
            key: MAVLinkNetworkSpecification.from_json(value, id=key)
            for key, value in network_specs.items()
        }

    def _on_rtk_correction_packet(self, sender, packet: bytes):
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in all the networks belonging to the extension.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                all the networks belonging to the extension
        """
        if not self._networks:
            return

        for name, network in self._networks.items():
            try:
                network.enqueue_rtk_correction_packet(packet)
            except Exception:
                if self.log:
                    self.log.warn(
                        f"Failed to enqueue RTK correction packet to network {name!r}"
                    )

    def _on_show_configuration_changed(self, sender, config) -> None:
        """Handler that is called when the user changes the start time or start
        method of the drones in the `show` extension.
        """
        if not self._networks:
            return

        # Make a copy of the configuration in case someone else who comes after
        # us in the handler chain messes with it
        config = config.clone()

        # Send the configuration to all the networks
        self._update_show_configuration_in_networks(config)

    def _on_show_light_configuration_changed(self, sender, config) -> None:
        """Handler that is called when the user changes the LED light configuration
        of the drones in the `show` extesion.
        """
        if not self._networks:
            return

        # Make a copy of the configuration in case someone else who comes after
        # us in the handler chain messes with it
        config = config.clone()

        # Send the configuration to all the networks
        self._update_show_light_configuration_in_networks(config)

    def _register_uav(self, uav: UAV) -> None:
        """Registers a new UAV object in the object registry of the application
        in a manner that ensures that the UAV is unregistered when the extension
        is stopped.
        """
        if self._uavs is None:
            raise RuntimeError("cannot register a UAV before the extension is started")

        try:
            self.app.object_registry.add(uav)
        except RegistryFull:
            # User reached the license limit, this is okay, we still keep track
            # of the UAV ourselves but it won't appear in the object registry
            pass

        self._uavs.append(uav)

    async def _send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        wait_for_response: Optional[MAVLinkMessageSpecification] = None,
        wait_for_one_of: Optional[Dict[str, MAVLinkMessageMatcher]] = None,
        channel: Optional[str] = None,
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
            channel: specifies the channel that the packet should be sent on;
                defaults to the preferred channel of the network
        """
        if not self._networks:
            raise RuntimeError("Cannot send packet; extension is not running")

        network = self._networks[target.network_id]
        return await network.send_packet(
            spec, target, wait_for_response, wait_for_one_of, channel
        )

    def _update_show_configuration_in_networks(self, config=None, app=None) -> None:
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

    def _update_show_light_configuration_in_networks(
        self, config=None, app=None
    ) -> None:
        """Updates the current LED light settings of the drones managed by this
        extension, based on the given configuration object from the `show`
        extension. If the configuration object is `None`, retrieves it from the
        `show` extension itself.
        """
        if config is None:
            if app is None:
                raise RuntimeError(
                    "'app' kwarg must be provided if 'config' is missing"
                )

            config = app.import_api("show").get_light_configuration()

        for name, network in self._networks.items():
            try:
                network.notify_led_light_config_changed(config)
            except Exception:
                self.log.warn(
                    f"Failed to update LED light configuration of drones in network {name!r}"
                )


construct = MAVLinkDronesExtension
dependencies = ("show", "signals")
description = "Support for drones that use the MAVLink protocol"
schema = {
    "properties": {
        "connections": {
            "title": "Connection URLs",
            "type": "array",
            "format": "table",
            "items": {"type": "string"},
            "description": (
                "URLs describing the connections where the server needs to "
                "listen for incoming MAVLink packets. 'default' means that "
                "incoming MAVLink packets are expected on UDP port 14550 and "
                "outbound MAVLink packets are sent to UDP port 14555."
            ),
        },
        "custom_mode": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "required": False,
            "title": "Enforce MAVLink custom flight mode",
            "description": (
                "MAVLink custom flight mode number to switch drones to when "
                "they are discovered the first time. 127 is the mode number of "
                "the drone show mode for Skybrush-compatible MAVLink-based "
                "drones. Refer to the documentation of your autopilot for more "
                "details."
            ),
            "default": 127,
        },
        "id_format": {
            "type": "string",
            "default": "{0:02}",
            "title": "ID format",
            "description": "Python format string that determines the format of the IDs of the drones created by this extension.",
        },
        "routing": {
            "type": "object",
            "title": "Message routing",
            "properties": {
                "rtk": {
                    "type": "integer",
                    "title": "RTK messages",
                    "description": "Index of the connection where RTK correction messages are routed to (zero-based)",
                    "default": 0,
                    "minimum": 0,
                }
            },
        },
        "statustext_targets": {
            "type": "array",
            "title": "STATUSTEXT message handling",
            "items": {
                "type": "string",
                "enum": ["client", "server"],
                "options": {
                    "enum_titles": [
                        "Forward STATUSTEXT messages to Skybrush clients",
                        "Log STATUSTEXT messages in the server log",
                    ]
                },
            },
            "default": ["client", "server"],
            "uniqueItems": True,
        },
        "system_id": {
            "title": "System ID",
            "description": "MAVLink system ID of the server; typically IDs from 251 to 254 are reserved for ground stations.",
            "type": "integer",
            "minimum": 1,
            "maximum": 255,
            "default": 254,
        },
        # connections
        # routing
        # networks is an advanced setting and is not included here
        # packet_loss is an advanced setting and is not included here
    }
}
