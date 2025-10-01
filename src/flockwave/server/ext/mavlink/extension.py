"""Skybrush server extension that adds support for drone flocks using the
MAVLink protocol.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from functools import partial
from typing import (
    Any,
    Callable,
    Iterator,
    cast,
    Optional,
    TYPE_CHECKING,
    overload,
)

from flockwave.server.ext.base import UAVExtension
from flockwave.server.ext.mavlink.fw_upload import FirmwareUpdateTarget
from flockwave.server.model.uav import UAV
from flockwave.server.registries.errors import RegistryFull
from flockwave.server.utils import optional_int, overridden

from .autopilots import ArduPilot, ArduPilotWithSkybrush, PX4, Autopilot
from .driver import MAVLinkDriver, MAVLinkUAV
from .errors import InvalidSigningKeyError
from .network import MAVLinkNetwork
from .rssi import RSSIMode
from .rtk import RTKCorrectionPacketSignalManager
from .tasks import check_uavs_alive
from .types import (
    MAVLinkMessage,
    MAVLinkMessageMatcher,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
    MAVLinkStatusTextTargetSpecification,
)

if TYPE_CHECKING:
    from flockwave.server.ext.rc import RCState
    from flockwave.server.ext.show.clock import ShowClock
    from flockwave.server.ext.show.config import DroneShowConfiguration
    from flockwave.server.tasks.led_lights import LightConfiguration


#: Dictionary that resolves common connection preset aliases used in
#: the configuration file
CONNECTION_PRESETS = {
    "default": "udp-listen://:14550?broadcast_port=14555",
    "apm-sitl": "tcp://localhost:5760",
}

#: Default routing configuration for networks
DEFAULT_ROUTING = {"rtk": 0, "rc": 0}


class MAVLinkDronesExtension(UAVExtension[MAVLinkDriver]):
    """Extension that adds support for drone flocks using the MAVLink
    protocol.
    """

    _networks: dict[str, MAVLinkNetwork]
    """Dictionary mapping network IDs to the MAVLink networks managed by this
    extension.
    """

    def __init__(self):
        super().__init__()

        self._driver = None
        self._networks = {}
        self._start_method = None
        self._uavs = None

    def _create_driver(self):
        return MAVLinkDriver()

    def configure_driver(self, driver: MAVLinkDriver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver: the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        assert self.log is not None

        assume_data_streams_configured = bool(
            configuration.get("assume_data_streams_configured")
        )
        if assume_data_streams_configured:
            self.log.info("MAVLink data streams are assumed to be configured")

        autopilot_type = str(configuration.get("autopilot_type", "auto")) or "auto"
        autopilot_factory: Optional[Callable[[], Autopilot]]
        match autopilot_type:
            case "auto":
                autopilot_factory = None
            case "ardupilot":
                autopilot_factory = ArduPilot
                self.log.info("Flight controller firmware: ArduPilot")
            case "px4":
                autopilot_factory = PX4
                self.log.info("Flight controller firmware: PX4")
            case "skybrush":
                autopilot_factory = ArduPilotWithSkybrush
                self.log.info("Flight controller firmware: Skybrush on ArduPilot")
            case _:
                autopilot_factory = None
                self.log.warning(
                    f'Unknown flight controller firmware: {autopilot_type}, assuming "auto"'
                )

        use_bulk_parameter_uploads = bool(
            configuration.get("use_bulk_parameter_uploads", False)
        )
        if use_bulk_parameter_uploads:
            self.log.info("Using bulk parameter uploads (experimental)")

        driver.assume_data_streams_configured = assume_data_streams_configured
        driver.autopilot_factory = autopilot_factory
        driver.broadcast_packet = self._broadcast_packet
        driver.create_device_tree_mutator = self.create_device_tree_mutation_context
        driver.log = self.log
        driver.mandatory_custom_mode = optional_int(configuration.get("custom_mode"))
        driver.run_in_background = self.run_in_background
        driver.send_packet = self._send_packet
        driver.use_bulk_parameter_uploads = use_bulk_parameter_uploads

    def exports(self) -> dict[str, Any]:
        return {
            "find_network_by_id": self._find_network_by_id,
        }

    def _find_network_by_id(self, network_id: str) -> Optional[MAVLinkNetwork]:
        """Finds a MAVLink network managed by this extension by its ID.

        Parameters:
            network_id: the ID of the network to find

        Returns:
            the MAVLink network with the given ID, or `None` if no such
            network exists
        """
        return self._networks.get(network_id)

    async def run(self, app, configuration):
        networks = OrderedDict(
            (network_id, MAVLinkNetwork.from_specification(spec))
            for network_id, spec in self._get_network_specifications_from_configuration(
                configuration
            ).items()
        )

        # Get a handle to the signals extension that we will need
        signals = app.import_api("signals")
        status_summary_signal = signals.get("mavlink:status_summary")

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
            "supervisor": app.supervise,
            "use_connection": app.connection_registry.use,
        }

        # Create an object responsible for distributing RTK correction packets
        # to other extensions that are interested in them
        rtk_correction_packet_signal_manager = RTKCorrectionPacketSignalManager()

        # Create a cleanup context and run the extension
        with ExitStack() as stack:
            stack.enter_context(overridden(self, _uavs=uavs, _networks=networks))

            # Connect the signals to our signal handlers
            stack.enter_context(
                signals.use(
                    {
                        "rc:changed": self._on_rc_channels_changed,
                        "rtk:packet": self._on_rtk_correction_packet,
                        "show:clock_changed": self._on_show_clock_changed,
                        "show:config_updated": self._on_show_configuration_changed,
                        "show:lights_updated": self._on_show_light_configuration_changed,
                    }
                )
            )

            # Connect the RTK correction packet signal manager to the signals API
            # so it knows which signal to dispatch when a new RTK correction
            # packet is to be forwarded to other extensions
            stack.enter_context(
                rtk_correction_packet_signal_manager.use(signals, log=self.log)
            )

            # Forward the current start configuration for the drones in this network.
            # Note that this can be called only if self._networks has been set
            # up so we cannot do it outside the exit stack
            self._update_show_configuration_in_networks()
            self._update_show_start_time_in_networks()

            # Also forward the current lights configuration for the drones in
            # this network.
            self._update_show_light_configuration_in_networks()

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

    @staticmethod
    @contextmanager
    def use_firmware_update_support(api) -> Iterator[None]:
        """Enhancer context manager that adds support for remote firmware updates
        to virtual UAVs.
        """
        with ExitStack() as stack:
            for target_id in FirmwareUpdateTarget:
                target = api.create_target(
                    id=target_id.value, name=target_id.describe()
                )
                stack.enter_context(api.use_target(target))
            yield

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
    ) -> dict[str, MAVLinkNetworkSpecification]:
        assert self.log is not None

        # Construct the network specifications first
        if "networks" in configuration:
            if "connections" in configuration:
                self.log.warning(
                    "Move the 'connections' configuration key inside a network; "
                    + "'connections' ignored when 'networks' is present"
                )
            network_specs = configuration["networks"]
        else:
            self.log.warning(
                "The top-level 'connections' key in the configuration of the "
                "MAVLink extension is deprecated; move it under 'networks.mav' "
                "to get rid of this warning."
            )
            network_specs = {
                "mav": {"connections": configuration.get("connections", ())}
            }

        # Filter null values from network_specs; these are used to delete
        # networks from the default configuration
        network_specs: dict[str, dict] = {
            k: v for k, v in network_specs.items() if isinstance(v, dict)
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
            "rssi_mode": configuration.get("rssi_mode", RSSIMode.RADIO_STATUS.value),
            "statustext_targets": configuration.get(
                "statustext_targets", MAVLinkStatusTextTargetSpecification.DEFAULT.json
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

        # Return the network specifications, ordered by ID. This is to ensure
        # that integer network indices are handed out in a consistent manner.
        result: OrderedDict[str, MAVLinkNetworkSpecification] = OrderedDict()
        for key in sorted(network_specs.keys()):
            try:
                result[key] = MAVLinkNetworkSpecification.from_json(
                    network_specs[key], id=key
                )
            except InvalidSigningKeyError as ex:
                self.log.warning(
                    f"Ignoring network. Cause: {ex}",
                    extra={"id": key},
                )

        return result

    def _on_rc_channels_changed(self, sender: "RCState"):
        """Handles the event when the RC channel values changed."""
        if not self._networks:
            return

        if sender.lost:
            # Cancel all previous RC overrides. For channels <= 8, zero means
            # "release back to RC radio". For channels > 8, 65534 means
            # "release back to rC radio" as zero would mean "ignore"
            channels = [0] * 8 + [65534] * 10
        else:
            # Get scaled PWM values to send
            channels = sender.get_scaled_channel_values_int(out_of_range=0)
            if sender.num_channels < 18:
                # Ignore channels for which the sender has no real value.
                # 65535 in MAVLink RC_CHANNELS_OVERRIDE packets means "ignore"
                num_missing = 18 - sender.num_channels
                channels[sender.num_channels :] = [65535] * num_missing

        for name, network in self._networks.items():
            try:
                network.enqueue_rc_override_packet(channels)
            except Exception:
                if self.log:
                    self.log.warning(
                        f"Failed to enqueue RC override packet to network {name!r}"
                    )

    def _on_rtk_correction_packet(self, sender, packet: bytes):
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in all the networks belonging to the extension.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                all the networks belonging to the extension
        """
        if not self._networks:
            return

        # Forward the packet to all networks
        for name, network in self._networks.items():
            try:
                network.enqueue_rtk_correction_packet(packet)
            except Exception:
                if self.log:
                    self.log.warning(
                        f"Failed to enqueue RTK correction packet to network {name!r}"
                    )

    def _on_show_clock_changed(self, sender) -> None:
        """Handler that is called when the show clock is started, stopped or
        adjusted.
        """
        self._update_show_start_time_in_networks()

    def _on_show_configuration_changed(
        self, sender, config: DroneShowConfiguration
    ) -> None:
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

    def _on_show_light_configuration_changed(
        self, sender, config: LightConfiguration
    ) -> None:
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
        assert self.app is not None

        if self._uavs is None:
            raise RuntimeError("cannot register a UAV before the extension is started")

        try:
            self.app.object_registry.add(uav)
        except RegistryFull:
            # User reached the license limit, this is okay, we still keep track
            # of the UAV ourselves but it won't appear in the object registry
            self.app.handle_registry_full_error(self, "MAVLink UAV")

        self._uavs.append(uav)

    @overload
    async def _send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        *,
        channel: Optional[str] = None,
    ) -> None: ...

    @overload
    async def _send_packet(
        self,
        spec: Optional[MAVLinkMessageSpecification],
        target: MAVLinkUAV,
        *,
        wait_for_response: tuple[str, MAVLinkMessageMatcher],
        channel: Optional[str] = None,
    ) -> MAVLinkMessage: ...

    @overload
    async def _send_packet(
        self,
        spec: Optional[MAVLinkMessageSpecification],
        target: MAVLinkUAV,
        *,
        wait_for_one_of: dict[str, MAVLinkMessageSpecification],
        channel: Optional[str] = None,
    ) -> tuple[str, MAVLinkMessage]: ...

    async def _send_packet(
        self,
        spec: Optional[MAVLinkMessageSpecification],
        target: MAVLinkUAV,
        *,
        wait_for_response: Optional[tuple[str, MAVLinkMessageMatcher]] = None,
        wait_for_one_of: Optional[dict[str, MAVLinkMessageSpecification]] = None,
        channel: Optional[str] = None,
    ):
        """Sends a message to the given UAV and optionally waits for a matching
        response.

        Parameters:
            spec: the specification of the MAVLink message to send; ``None`` if
                no packet needs to be sent and we only need to wait for a reply
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

        Returns:
            ``None`` if `wait_for_response` and `wait_for_one_of` are both
            ``None``; the received response if `wait_for_response` was not
            ``None``; the key of the matched message specification and the
            message itself if `wait_for_one_of` was not ``None``.
        """
        if not self._networks:
            raise RuntimeError("Cannot send packet; extension is not running")

        network = self._networks[target.network_id]
        return await network.send_packet(
            spec,
            target,
            wait_for_response=wait_for_response,
            wait_for_one_of=wait_for_one_of,
            channel=channel,
        )

    def _update_show_configuration_in_networks(
        self, config: Optional[DroneShowConfiguration] = None
    ) -> None:
        """Updates the start method of the drones managed by this extension,
        based on the given configuration object from the `show` extension. If
        the configuration object is `None`, retrieves it from the `show`
        extension itself.
        """
        assert self.app is not None
        assert self.log is not None

        if config is None:
            config = self.app.import_api("show").get_configuration()

        for name, network in self._networks.items():
            try:
                network.notify_scheduled_takeoff_config_changed(config)  # type: ignore
            except Exception:
                self.log.warning(
                    f"Failed to update start configuration of drones in network {name!r}"
                )

    def _update_show_start_time_in_networks(self) -> None:
        """Updates the scheduled start times of the drones managed by this
        extension, based on the start time extracted from the show clock.
        """
        assert self.app is not None
        assert self.log is not None

        clock = cast(Optional["ShowClock"], self.app.import_api("show").get_clock())
        if not clock:
            return

        for name, network in self._networks.items():
            try:
                network.notify_show_clock_start_time_changed(clock.start_time)
            except Exception:
                self.log.warning(
                    f"Failed to update start time of drones in network {name!r}"
                )

    def _update_show_light_configuration_in_networks(
        self, config: LightConfiguration | None = None
    ) -> None:
        """Updates the current LED light settings of the drones managed by this
        extension, based on the given configuration object from the `show`
        extension. If the configuration object is `None`, retrieves it from the
        `show` extension itself.
        """
        assert self.app is not None
        assert self.log is not None

        if config is None:
            config = cast(
                "LightConfiguration",
                self.app.import_api("show").get_light_configuration(),
            )

        for name, network in self._networks.items():
            try:
                network.notify_led_light_config_changed(config)
            except Exception:
                self.log.warning(
                    f"Failed to update LED light configuration of drones in network {name!r}"
                )
