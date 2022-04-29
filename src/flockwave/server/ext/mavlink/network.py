"""Classes and functions related to MAVLink networks, i.e. a set of MAVLink
connections over which the system IDs of the devices share the same namespace.

For instance, a Skybrush server may participate in a MAVLink network with two
connections: a wifi connection and a fallback radio connection. The system ID
of a MAVLink message received on either of these two connections refers to the
same device. However, the same system ID in a different MAVLink network may
refer to a completely different device. The introduction of the concept of
MAVLink networks in the Skybrush server will allow us in the future to manage
multiple independent MAVLink-based drone swarms.
"""

from collections import defaultdict
from contextlib import contextmanager, ExitStack
from logging import Logger
from time import time_ns
from trio import move_on_after, open_nursery, to_thread
from trio.abc import ReceiveChannel
from trio_util import periodic
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from flockwave.connections import Connection, create_connection, ListenerConnection
from flockwave.concurrency import Future, race
from flockwave.networking import find_interfaces_with_address
from flockwave.server.comm import CommunicationManager
from flockwave.server.model import ConnectionPurpose
from flockwave.server.utils import nop, overridden

from .comm import (
    create_communication_manager,
    Channel,
    MAVLinkMessage,
)
from .driver import MAVLinkDriver, MAVLinkUAV
from .enums import MAVAutopilot, MAVComponent, MAVMessageType, MAVState, MAVType
from .led_lights import MAVLinkLEDLightConfigurationManager
from .packets import DroneShowStatus
from .rtk import RTKCorrectionPacketEncoder
from .takeoff import ScheduledTakeoffManager
from .types import (
    MAVLinkMessageMatcher,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
)
from .utils import (
    flockwave_severity_from_mavlink_severity,
    python_log_level_from_mavlink_severity,
    log_id_from_message,
)

__all__ = ("MAVLinkNetwork",)

#: MAVLink message specification for heartbeat messages that we are sending
#: to connected UAVs to keep them sending telemetry data
HEARTBEAT_SPEC = (
    "HEARTBEAT",
    {
        "type": MAVType.GCS,
        "autopilot": MAVAutopilot.INVALID,
        "base_mode": 0,
        "custom_mode": 0,
        "system_status": MAVState.STANDBY,
    },
)


class MAVLinkNetwork:
    """Representation of a MAVLink network."""

    driver: MAVLinkDriver
    log: Logger
    manager: CommunicationManager[MAVLinkMessageSpecification, Any]

    _connections: List[Connection]
    _statustext_targets: FrozenSet[str]
    _uav_addresses: Dict[MAVLinkUAV, Any]
    _uavs: Dict[str, MAVLinkUAV]

    @classmethod
    def from_specification(cls, spec: MAVLinkNetworkSpecification):
        """Creates a MAVLink network from its specification, typically found in
        a configuration file.
        """
        result = cls(
            spec.id,
            system_id=spec.system_id,
            id_formatter=spec.id_format.format,
            packet_loss=spec.packet_loss,
            statustext_targets=spec.statustext_targets,
            routing=spec.routing,
        )

        for connection_spec in spec.connections:
            connection = create_connection(connection_spec)
            result.add_connection(connection)

        return result

    def __init__(
        self,
        id: str,
        *,
        system_id: int = 255,
        id_formatter: Callable[[str, str], str] = "{0}".format,
        packet_loss: float = 0,
        statustext_targets: Optional[FrozenSet[str]] = None,
        routing: Optional[Dict[str, int]] = None,
    ):
        """Constructor.

        Creates a new MAVLink network with the given network ID. Network
        identifiers must be unique in the Skybrush server.

        Parameters:
            id: the network ID
            system_id: the MAVLink system ID of the Skybrush server within the
                network
            id_formatter: function that can be called with a MAVLink system ID
                and the network ID, and that must return a string that will be
                used for the drone with the given system ID on the network
            packet_loss: when larger than zero, simulates packet loss on the
                network by randomly dropping received and sent MAVLink messages
            statustext_targets: specifies where to forward MAVLink status text
                messages. When the set contains the string `"server"`, the
                status messages will be sent to the server log. When the
                set contains the string `"client"`, they will be sent to the
                connected clients in SYS-MSG messages. The two options are not
                mutually exclusive; both can be configured at the same time.
            routing: dictionary that specifies which link should be used for
                certain types of packets. The keys of the dictionary are the
                packet types; the values are the indices of the connections to
                use for sending that particular packet type. Not including a
                particular packet type in the dictionary will let the system
                choose the link on its own.
        """
        self.log = None  # type: ignore

        self._id = id
        self._id_formatter = id_formatter
        self._led_light_configuration_manager = MAVLinkLEDLightConfigurationManager(
            self
        )
        self._matchers = None
        self._packet_loss = max(float(packet_loss), 0.0)
        self._routing = dict(routing or {})
        self._scheduled_takeoff_manager = ScheduledTakeoffManager(self)
        self._statustext_targets = (
            frozenset(statustext_targets) if statustext_targets else frozenset()
        )
        self._system_id = max(min(int(system_id), 255), 1)

        self._connections = []
        self._uavs = {}
        self._uav_addresses = {}

        self._rtk_correction_packet_encoder = RTKCorrectionPacketEncoder()
        self._rtk_packet_fragments_signal = None

    def add_connection(self, connection: Connection):
        """Adds the given connection object to this network.

        Parameters:
            connection: the connection to add
        """
        self._connections.append(connection)

    @contextmanager
    def expect_packet(
        self,
        type: Union[int, str, MAVMessageType],
        params: MAVLinkMessageMatcher = None,
        system_id: Optional[int] = None,
    ) -> Iterator[Future[MAVLinkMessage]]:
        """Sets up a handler that waits for a MAVLink packet of a given type,
        optionally matching its content with the given parameter values based
        on strict equality.

        Parameters:
            type: the type of the MAVLink message to wait for
            params: dictionary mapping parameter names to the values that we
                expect to see in the matched packet, or a callable that
                receives a MAVLinkMessage and returns `True` if it matches the
                packet we are looking for
            system_id: the system ID of the sender of the message; `None` means
                any system ID

        Returns:
            a Future that resolves to the next MAVLink message that matches the
            given type and parameter values.
        """
        type_str = type if isinstance(type, str) else MAVMessageType(type).name

        # Map values of type 'bytes' to 'str' in the params dict because
        # pymavlink never returns 'bytes'
        if params is not None and not callable(params):
            for name, value in params.items():
                if isinstance(value, bytes):
                    try:
                        params[name] = value.decode("utf-8")
                    except ValueError:
                        pass

        future = Future()
        item = (system_id, params, future)
        matchers = self._matchers[type_str]

        matchers.append(item)
        try:
            yield future
        finally:
            matchers.pop(matchers.index(item))

    @property
    def id(self) -> str:
        """The unique identifier of this MAVLink network."""
        return self._id

    async def run(
        self,
        *,
        driver,
        log,
        register_uav,
        rtk_packet_fragments_signal,
        supervisor,
        use_connection,
    ):
        """Starts the network manager.

        Parameters:
            driver: the driver object for MAVLink-based drones
            log: a logging object where the network manager can log messages
            register_uav: a callable that can be called with a single UAV_
                object as an argument to get it registered in the application
            rtk_packet_fragments_signal: signal to emit when an RTK correction
                packet has been fragmented into MAVLink packets and is about to
                be sent to the networks. Can be used to implement a secondary
                backup channel for RTK corrections; see also the `sidekick`
                extension
            supervisor: the application supervisor that can be used to re-open
                connections if they get closed
            use_connection: context manager that must be entered when the
                network manager wishes to register a connection in the
                application
        """
        if len(self._connections) > 1:
            if self.id:
                id_format = "{0}/{1}"
            else:
                id_format = "{1}"
        else:
            id_format = "{0}"

        # Create names for the communication links
        connection_names = [
            id_format.format(self.id, index)
            for index, connection in enumerate(self._connections)
        ]

        # Register the communication links
        with ExitStack() as stack:
            for connection, name in zip(self._connections, connection_names):
                description = (
                    "MAVLink listener"
                    if isinstance(connection, ListenerConnection)
                    else "MAVLink connection"
                )
                if name:
                    description += f" ({name})"

                stack.enter_context(
                    use_connection(
                        connection,
                        f"MAVLink: {name}" if name else "MAVLink",
                        description=description,
                        purpose=ConnectionPurpose.uavRadioLink,
                    )
                )

            # Create the communication manager
            manager = create_communication_manager(
                packet_loss=self._packet_loss, system_id=self._system_id
            )

            # Warn the user about the simulated packet loss setting
            if self._packet_loss > 0:
                percentage = round(min(1, self._packet_loss) * 100)
                log.warn(
                    f"Simulating {percentage}% packet loss on MAVLink network {self._id!r}"
                )

            # Register the links with the communication manager. The order is
            # important here; the ones coming first will primarily be used for
            # sending, falling back to later ones if sending on the first one
            # fails
            for connection, name in zip(self._connections, connection_names):
                manager.add(connection, name=name)

            # Register the connection aliases
            self._register_connection_aliases(manager, connection_names, stack, log=log)

            # Set up a dictionary that will map from MAVLink message types that
            # we are waiting for to lists of corresponding (predicate, future)
            # pairs
            matchers = defaultdict(list)

            # Override some of our properties with the values we were called with
            stack.enter_context(
                overridden(
                    self,
                    log=log,
                    driver=driver,
                    manager=manager,
                    register_uav=register_uav,
                    _matchers=matchers,
                    _rtk_packet_fragments_signal=rtk_packet_fragments_signal,
                )
            )

            async with open_nursery() as nursery:
                # Start background tasks that check the configured start times
                # on the drones at regular intervals and that take care of
                # broadcasting the current light configuration to the drones
                nursery.start_soon(self._scheduled_takeoff_manager.run)
                nursery.start_soon(self._led_light_configuration_manager.run)

                # Start the communication manager
                try:
                    await manager.run(
                        consumer=self._handle_inbound_messages,
                        supervisor=supervisor,
                        log=log,
                        tasks=[self._generate_heartbeats],
                    )
                finally:
                    for matcher in matchers.values():
                        for _, _, future in matcher:
                            future.cancel()

                # Cancel all tasks in this nursery as we are about to shut down
                nursery.cancel_scope.cancel()

    async def broadcast_packet(
        self, spec: MAVLinkMessageSpecification, channel: Optional[str] = None
    ) -> None:
        """Broadcasts a message to all UAVs in the network.

        Parameters:
            spec: the specification of the MAVLink message to send
            channel: specifies the channel that the packet should be sent on;
                defaults to the primary channel of the network
        """
        await self.manager.broadcast_packet(spec, destination=channel)

    def enqueue_rtk_correction_packet(self, packet: bytes) -> None:
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in this network.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                this network
        """
        if not self.manager:
            return

        messages = []
        for message in self._rtk_correction_packet_encoder.encode(packet):
            self.manager.enqueue_broadcast_packet(
                message, destination=Channel.RTK, allow_failure=True
            )
            messages.append(message)

        if messages and self._rtk_packet_fragments_signal:
            try:
                self._rtk_packet_fragments_signal.send(self, messages=messages)
            except Exception:
                # We do not take responsibility for exceptions thrown in the
                # signal handlers
                if self.log:
                    self.log.exception(
                        "RTK packet fragment signal handler threw an exception"
                    )

    def notify_led_light_config_changed(self, config):
        """Notifies the network that the LED light configuration of the drones
        has changed in the system. The network will then update the LED light
        configuration of each drone.
        """
        self._led_light_configuration_manager.notify_config_changed(config)

    def notify_scheduled_takeoff_config_changed(self, config):
        """Notifies the network that the automatic start configuration of the
        drones has changed in the system. The network will then update the
        start configuration of each drone.
        """
        self._scheduled_takeoff_manager.notify_config_changed(config)

    async def send_heartbeat(self, target: MAVLinkUAV) -> Optional[MAVLinkMessage]:
        """Sends a heartbeat targeted to the given UAV.

        It is assumed (and not checked) that the UAV belongs to this network.

        Parameters:
            target: the UAV to send the heartbeat to
        """
        spec = HEARTBEAT_SPEC
        address = self._uav_addresses.get(target)
        if address is None:
            raise RuntimeError("UAV has no address in this network")

        destination = (Channel.PRIMARY, address)
        await self.manager.send_packet(spec, destination)

    async def send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        wait_for_response: Optional[Tuple[str, MAVLinkMessageMatcher]] = None,
        wait_for_one_of: Optional[Dict[str, MAVLinkMessageMatcher]] = None,
        channel: Optional[str] = None,
    ) -> Optional[MAVLinkMessage]:
        """Sends a message to the given UAV and optionally waits for a matching
        response.

        It is assumed (and not checked) that the UAV belongs to this network.

        Parameters:
            spec: the specification of the MAVLink message to send
            target: the UAV to send the message to
            wait_for_response: when not `None`, specifies a MAVLink message
                type to wait for as a response, and an additional message
                matcher that examines the message further to decide whether this
                is really the response we are interested in. The matcher may be
                `None` to match all messages of the given type, a dictionary
                mapping MAVLink field names to expected values, or a callable
                that gets called with the retrieved MAVLink message of the
                given type and must return `True` if and only if the message
                matches our expectations. The source system of the MAVLink
                reply must also be equal to the system ID of the UAV where
                the original message was sent.
            channel: specifies the channel that the packet should be sent on;
                defaults to the primary channel of the network
        """
        spec[1].update(
            target_system=target.system_id,
            target_component=MAVComponent.AUTOPILOT1,
            _mavlink_version=target.mavlink_version,
        )

        address = self._uav_addresses.get(target)
        if address is None:
            raise RuntimeError("UAV has no address in this network")

        destination = (channel or Channel.PRIMARY, address)

        if wait_for_response:
            response_type, response_fields = wait_for_response
            with self.expect_packet(
                response_type, response_fields, system_id=target.system_id
            ) as future:
                # TODO(ntamas): in theory, we could be getting a matching packet
                # _before_ we sent ours. Sort this out if it causes problems.
                await self.manager.send_packet(spec, destination)
                return await future.wait()

        elif wait_for_one_of:
            tasks = {}

            with ExitStack() as stack:
                # Prepare futures for every single message type that we expect,
                # and then send the message itself
                for key, (response_type, response_fields) in wait_for_one_of.items():
                    future = stack.enter_context(
                        self.expect_packet(
                            response_type, response_fields, system_id=target.system_id
                        )
                    )
                    tasks[key] = future.wait

                # Now send the message and wait for _any_ of the futures to
                # succeed
                await self.manager.send_packet(spec, destination)
                return await race(tasks)
        else:
            await self.manager.send_packet(spec, destination)

    def uavs(self) -> Iterable[MAVLinkUAV]:
        """Returns an iterator that iterates over the UAVs in this network.

        Make sure that you do not add new UAVs or remove existing ones while the
        iteration takes place.
        """
        return self._uavs.values() if self._uavs else []

    def _create_uav(self, system_id: str) -> MAVLinkUAV:
        """Creates a new UAV with the given system ID in this network and
        registers it in the UAV registry.
        """
        uav_id = self._id_formatter(system_id, self.id)

        self._uavs[system_id] = uav = self.driver.create_uav(uav_id)
        uav.assign_to_network_and_system_id(self.id, system_id)

        self.register_uav(uav)

        return uav

    def _find_uav_from_message(
        self, message: MAVLinkMessage, address: Any
    ) -> Optional[MAVLinkUAV]:
        """Finds the UAV that this message is sent from, based on its system ID,
        creating a new UAV object if we have not seen the UAV yet.

        Parameters:
            message: the message
            address: the address that the message was sent from

        Returns:
            the UAV belonging to the system ID of the message or `None` if the
            message was a broadcast message
        """
        system_id = message.get_srcSystem()
        if system_id == 0:
            return None
        else:
            uav = self._uavs.get(system_id)
            if not uav:
                uav = self._create_uav(system_id)

            # TODO(ntamas): protect from address hijacking!
            self._uav_addresses[uav] = address

            return uav

    async def _generate_heartbeats(self, manager: CommunicationManager):
        """Generates heartbeat messages on the channels corresponding to the
        network.
        """
        async for _ in periodic(1):
            await manager.broadcast_packet(
                HEARTBEAT_SPEC, destination=Channel.PRIMARY, allow_failure=True
            )

    async def _handle_inbound_messages(self, channel: ReceiveChannel):
        """Handles inbound MAVLink messages from all the communication links
        that the extension manages.

        Parameters:
            channel: a Trio receive channel that yields inbound MAVLink messages.
        """
        handlers = {
            "AUTOPILOT_VERSION": self._handle_message_autopilot_version,
            "BAD_DATA": nop,
            "COMMAND_ACK": nop,
            "DATA16": self._handle_message_data16,
            "FENCE_STATUS": nop,
            "FILE_TRANSFER_PROTOCOL": nop,
            "GLOBAL_POSITION_INT": self._handle_message_global_position_int,
            "GPS_GLOBAL_ORIGIN": nop,
            "GPS_RAW_INT": self._handle_message_gps_raw_int,
            "HEARTBEAT": self._handle_message_heartbeat,
            "HOME_POSITION": nop,
            "HWSTATUS": nop,
            "LOCAL_POSITION_NED": nop,  # maybe later?
            "MAG_CAL_PROGRESS": self._handle_message_mag_cal_progress,
            "MAG_CAL_REPORT": self._handle_message_mag_cal_report,
            "MEMINFO": nop,
            "MISSION_ACK": nop,  # used for mission and geofence download / upload
            "MISSION_COUNT": nop,  # used for mission and geofence download / upload
            "MISSION_CURRENT": nop,  # maybe later?
            "MISSION_ITEM_INT": nop,  # used for mission and geofence download / upload
            "MISSION_REQUEST": nop,  # used for mission and geofence download / upload
            "NAV_CONTROLLER_OUTPUT": nop,
            "PARAM_VALUE": nop,
            "POSITION_TARGET_GLOBAL_INT": nop,
            "POWER_STATUS": nop,
            "STATUSTEXT": self._handle_message_statustext,
            "SYS_STATUS": self._handle_message_sys_status,
            "TIMESYNC": self._handle_message_timesync,
            "V2_EXTENSION": self._handle_message_v2_extension,
        }

        autopilot_component_id = MAVComponent.AUTOPILOT1

        # Many third-party MAVLink-based drones do not respond to broadcast
        # messages sent to them with an IP address of 255.255.255.255 as they
        # listen to the subnet-specific broadcast address only (e.g., 192.168.0.255).
        # Therefore, we need to re-bind the broadcast address of the channel
        # as soon as we have received the first packet from it, based on the
        # address of that packet and the netmasks of the network interfaces
        broadcast_address_updated = False

        async for connection_id, (message, address) in channel:
            if message.get_srcComponent() != autopilot_component_id:
                # We do not handle messages from any other component but an
                # autopilot
                continue

            # Uncomment this for debugging
            # self.log.info(repr(message))

            # Update the broadcast address to a subnet-specific one if needed
            if not broadcast_address_updated:
                await self._update_broadcast_address_of_channel_to_subnet(
                    connection_id, address
                )
                broadcast_address_updated = True

            # Get the message type
            type = message.get_type()

            # Resolve all futures that are waiting for this message
            for system_id, params, future in self._matchers[type]:
                if future.done():
                    # This may happen if we get multiple matching messages in
                    # quick succession before the task waiting for the result
                    # gets a chance of responding to them; in this case, we
                    # have to ignore the message, otherwise we would be resolving
                    # the future twice
                    continue
                if system_id is not None and message.get_srcSystem() != system_id:
                    matched = False
                elif callable(params):
                    matched = params(message)
                elif params is None:
                    matched = True
                else:
                    matched = all(
                        getattr(message, param_name, None) == param_value
                        for param_name, param_value in params.items()
                    )
                if matched:
                    future.set_result(message)

            # Call the message handler if we have one
            handler = handlers.get(type)
            if handler:
                try:
                    handler(message, connection_id=connection_id, address=address)
                except Exception:
                    self.log.exception(
                        f"Error while handling MAVLink message of type {type}"
                    )
            else:
                self.log.warn(
                    f"Unhandled MAVLink message type: {type}",
                    extra=self._log_extra_from_message(message),
                )
                handlers[type] = nop

    def _handle_message_autopilot_version(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_autopilot_version(message)

    def _handle_message_data16(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        if message.type == DroneShowStatus.TYPE:
            uav = self._find_uav_from_message(message, address)
            if uav:
                uav.handle_message_drone_show_status(message)

    def _handle_message_global_position_int(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_global_position_int(message)

    def _handle_message_gps_raw_int(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_gps_raw_int(message)

    def _handle_message_heartbeat(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink HEARTBEAT message."""
        if not MAVType(message.type).is_vehicle:
            # Ignore non-vehicle heartbeats
            return

        # Forward heartbeat to the appropriate UAV
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_heartbeat(message)

    def _handle_message_mag_cal_progress(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink MAG_CAL_PROGRESS message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_mag_cal_progress(message)

    def _handle_message_mag_cal_report(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink MAG_CAL_REPORT message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_mag_cal_report(message)

    def _handle_message_statustext(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink STATUSTEXT message and forwards it to the
        log console or to the GCS, depending on how the network is set up.
        """
        text = message.text
        if not text:
            return

        uav = self._find_uav_from_message(message, address)
        if uav and uav._autopilot.is_prearm_error_message(text):
            uav.notify_prearm_failure(uav._autopilot.process_prearm_error_message(text))

        for target in self._statustext_targets:
            if target == "server":
                extra = self._log_extra_from_message(message)
                extra["telemetry"] = "ignore"
                self.log.log(
                    python_log_level_from_mavlink_severity(message.severity),
                    text,
                    extra=extra,
                )
            elif target == "client":
                if uav:
                    uav.send_log_message_to_gcs(
                        text,
                        severity=flockwave_severity_from_mavlink_severity(
                            message.severity
                        ),
                    )

    def _handle_message_sys_status(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_sys_status(message)

    def _handle_message_timesync(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink TIMESYNC message."""
        if message.tc1 != 0:
            now = time_ns() // 1000
            self.log.info(f"Roundtrip time: {(now - message.ts1) // 1000} msec")
        else:
            # Timesync request, ignore it.
            pass

    def _handle_message_v2_extension(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink V2_EXTENSION message, currently used to
        carry debug information injected dynamically by a Skybrush MAVLink
        proxy. Not used in production.
        """
        if message.message_type != 42424:
            # Not for us
            return

        length = message.payload[0]
        payload = message.payload[1 : (length + 1)]
        self.log.info(repr(payload))

    def _log_message(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Logs an incoming MAVLink message for debugging purposes."""
        self.log.debug(str(message))

    def _log_extra_from_message(self, message: MAVLinkMessage) -> Dict[str, Any]:
        return {"id": log_id_from_message(message, self.id)}

    def _register_connection_aliases(
        self,
        manager: CommunicationManager,
        connection_names: Sequence[str],
        stack: ExitStack,
        log,
    ) -> None:
        """Registers some connection aliases in the given communication manager
        object so we can simply send RTK packets to an `Channel.RTK` alias and
        ensure that it ends up at the correct connection.

        This method is called automatically from the main task of this network
        during initialization; no need to call it manually.
        """
        if not self._connections:
            return

        def register_by_index(alias: str, index: Optional[int]) -> str:
            if index is None or index < 0 or index >= len(connection_names):
                # No such channel, just fall back to the primary one
                index = 0

            target = connection_names[index]
            stack.enter_context(manager.with_alias(alias, target=target))

            return target

        extra = {"id": self.id}

        # Register the first connection as the primary
        channel = register_by_index(Channel.PRIMARY, 0)
        secondary_channel = register_by_index(Channel.SECONDARY, 1)
        if channel != secondary_channel:
            log.info(
                f"Routing primary traffic to {channel}, falling back to {secondary_channel}",
                extra=extra,
            )
        else:
            log.info(f"Routing primary traffic to {channel}", extra=extra)

        # Register the RTK channel according to the routing setup
        channel = register_by_index(Channel.RTK, self._routing.get("rtk"))
        log.info(f"Routing RTK corrections to {channel}", extra=extra)

    async def _update_broadcast_address_of_channel_to_subnet(
        self, connection_id: str, address: Tuple[str, int], timeout: float = 1
    ) -> None:
        """Updates the broadcast address of the connection with the given ID to the
        subnet-specific broadcast address of the network interface that received
        a packet from the given address.
        """
        if isinstance(address, tuple):
            address, port = address
        else:
            # Not a wireless network
            return

        subnet = None
        with move_on_after(timeout):
            subnets = await to_thread.run_sync(find_interfaces_with_address, address)

        success = False
        if subnets:
            interface, subnet = subnets[0]
            # HACK HACK HACK this is an ugly temporary fix; we are reaching into
            # the internals of self.manager, which we shouldn't do
            for entries in self.manager._entries_by_name.values():
                for entry in entries:
                    if entry.channel and entry.name == connection_id:
                        broadcast_address = subnet.broadcast_address
                        if str(broadcast_address) == "127.255.255.255":
                            # We are on localhost, so just keep on using localhost
                            broadcast_address = "127.0.0.1"

                        old_broadcast_address = getattr(
                            entry.channel, "broadcast_address", None
                        )
                        if (
                            old_broadcast_address
                            and isinstance(old_broadcast_address, tuple)
                            and len(old_broadcast_address) == 2
                        ):
                            # Keep the port, update the address only
                            broadcast_port = old_broadcast_address[1]
                        else:
                            # Hmmm, let's just assume that the source port of this packet is okay
                            broadcast_port = port

                        entry.channel.broadcast_address = (
                            str(broadcast_address),
                            broadcast_port,
                        )
                        self.log.info(
                            f"Broadcast address updated to {broadcast_address}:{broadcast_port} "
                            f"({interface})",
                            extra={"id": self.id},
                        )
                        success = True

        if not success:
            self.log.warn("Failed to update broadcast address to a subnet-specific one")
