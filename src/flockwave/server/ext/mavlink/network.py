"""Classes and functions related to MAVLink networks, i.e. a set of MAVLink
connections over which the system IDs of the devices share the same namespace.

For instance, a Skybrush server may participate in a MAVLink network with two
connections: a wifi connection and a fallback radio connection. The system ID
of a MAVLink message received on either of these two connections refers to the
same device. However, the same system ID in a different MAVLink network may
refer to a completely different device. The introduction of the concept of
MAVLink networks in the Skybrush server allows us to manage multiple independent
MAVLink-based drone swarms.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager, ExitStack
from logging import Logger
from time import time_ns
from trio import move_on_after, open_nursery, to_thread
from trio.abc import ReceiveChannel
from trio_util import periodic
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Optional,
    Sequence,
    Union,
    TYPE_CHECKING,
)

from flockwave.connections import (
    Connection,
    create_connection,
    ListenerConnection,
    UDPListenerConnection,
)
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
from .errors import InvalidSystemIdError
from .led_lights import MAVLinkLEDLightConfigurationManager
from .packets import DroneShowStatus
from .rssi import RSSIMode
from .rtk import RTKCorrectionPacketEncoder
from .signing import MAVLinkSigningConfiguration
from .takeoff import MAVLinkScheduledTakeoffManager
from .types import (
    MAVLinkMessageMatcher,
    MAVLinkMessageSpecification,
    MAVLinkNetworkSpecification,
    MAVLinkStatusTextTargetSpecification,
    spec,
)
from .utils import (
    flockwave_severity_from_mavlink_severity,
    log_id_for_uav,
    python_log_level_from_mavlink_severity,
    log_id_from_message,
)

if TYPE_CHECKING:
    from flockwave.server.ext.show.config import DroneShowConfiguration
    from flockwave.server.tasks.led_lights import LightConfiguration

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


Matchers = dict[str, list[tuple[Optional[int], MAVLinkMessageMatcher, Future]]]


class MAVLinkNetwork:
    """Representation of a MAVLink network."""

    driver: MAVLinkDriver
    log: Logger
    register_uav: Callable[[MAVLinkUAV], None]
    manager: CommunicationManager[MAVLinkMessageSpecification, Any]

    _connections: list[Connection]
    _uav_addresses: dict[MAVLinkUAV, Any]

    _id: str
    """ID of the network."""

    _id_formatter: Callable[[int, str], str]
    """Formatter function that is called with the system ID of a UAV and the
    network ID and returns the final ID that should be assigned to the UAV in
    Skybrush.
    """

    _matchers: Matchers
    """Dictionary mapping MAVLink message types to lists of tuples consisting
    of an optional MAVLink system ID, a MAVLink message matching criterion and a
    future that will be resolved when a MAVLink message matching the criterion
    is received from the given MAVLink system ID (or any system ID if no
    system ID was specified).
    """

    _routing: dict[str, list[int]]

    _rssi_mode: RSSIMode
    """Specifies how this network derives RSSI values for the drones in the
    network.
    """

    _signing: MAVLinkSigningConfiguration
    """Object that stores how the MAVLink connections should handle signed
    messages (in both the inbound and the outbound directions).
    """

    _statustext_targets: MAVLinkStatusTextTargetSpecification
    """Object specifying how MAVLink STATUSTEXT messages should be handled."""

    _uav_system_id_offset: int = 0
    """Offset to add to the system ID of each UAV in the network before it is
    sent to the ID formatter that derives the final ID in Skybrush.
    """

    _uav_system_id_range: tuple[int, int] = (1, 256)
    """The range of system IDs that a UAV in this network can have. Closed from
    the left, open from the right.
    """

    _uavs: dict[int, MAVLinkUAV]
    """Dictionary mapping MAVLink system IDs in the network to the corresponding
    UAVs.
    """

    _use_broadcast_rate_limiting: bool = False
    """Whether to use artificial rate limiting for broadcast packets to work
    around flow control problems in certain types of links.

    Typically you can leave this at ``False`` unless you have packet loss
    problems and you suspect that they are due to buffer overflows in the
    links that the network uses.
    """

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
            rssi_mode=spec.rssi_mode,
            signing=spec.signing,
            uav_system_id_range=(1, spec.network_size + 1),
            uav_system_id_offset=spec.id_offset,
            use_broadcast_rate_limiting=spec.use_broadcast_rate_limiting,
        )

        for connection_spec in spec.connections:
            connection = create_connection(connection_spec)
            result.add_connection(connection)

        return result

    def __init__(
        self,
        id: str,
        *,
        system_id: int = 254,
        id_formatter: Callable[[int, str], str] = "{0}".format,
        packet_loss: float = 0,
        statustext_targets: MAVLinkStatusTextTargetSpecification = MAVLinkStatusTextTargetSpecification.DEFAULT,
        routing: Optional[dict[str, list[int]]] = None,
        rssi_mode: RSSIMode = RSSIMode.RADIO_STATUS,
        signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
        uav_system_id_offset: int = 0,
        uav_system_id_range: tuple[int, int] = (1, 256),
        use_broadcast_rate_limiting: bool = False,
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
            rssi_mode: specifies how this network will derive RSSI values for
                the drones in the network
            signing: object that specifies whether outgoing messages should be
                signed and whether incoming unsigned messages are accepted.
            uav_system_id_offset: offset to add to the system ID of each UAV
                before it is sent to the formatter function
            uav_system_id_range: tuple specifying the (inclusive) range of
                system IDs that a UAV in this network can have
        """
        system_id = int(system_id)
        if system_id < 1 or system_id > 255:
            raise ValueError("system_id must be between 1 and 255")

        self.log = None  # type: ignore
        self._matchers = None  # type: ignore

        self._id = id
        self._id_formatter = id_formatter
        self._packet_loss = max(float(packet_loss), 0.0)
        self._routing = routing or {}
        self._rssi_mode = rssi_mode
        self._signing = signing
        self._statustext_targets = statustext_targets
        self._system_id = system_id
        self._uav_system_id_offset = int(uav_system_id_offset)
        self._uav_system_id_range = uav_system_id_range
        self._use_broadcast_rate_limiting = bool(use_broadcast_rate_limiting)

        self._connections = []
        self._uavs = {}
        self._uav_addresses = {}

        self._led_light_configuration_manager = MAVLinkLEDLightConfigurationManager(
            self
        )
        self._scheduled_takeoff_manager = MAVLinkScheduledTakeoffManager(self)
        self._rtk_correction_packet_encoder = RTKCorrectionPacketEncoder()

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
        register_uav: Callable[[MAVLinkUAV], None],
        supervisor,
        use_connection,
    ):
        """Starts the network manager.

        Parameters:
            driver: the driver object for MAVLink-based drones
            log: a logging object where the network manager can log messages
            register_uav: a callable that can be called with a single UAV_
                object as an argument to get it registered in the application
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
                packet_loss=self._packet_loss,
                network_id=self.id,
                system_id=self._system_id,
                signing=self._signing,
                use_broadcast_rate_limiting=self._use_broadcast_rate_limiting,
            )

            # Warn the user about the simulated packet loss setting
            if self._packet_loss > 0:
                percentage = round(min(1, self._packet_loss) * 100)
                log.warning(
                    f"Simulating {percentage}% packet loss",
                    extra={"id": self._id},
                )

            # Warn the user when the rate limiting is enabled for broadcasts
            if self._use_broadcast_rate_limiting:
                log.info(
                    "Rate limiting enabled for broadcast packets",
                    extra={"id": self._id},
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
            matchers: Matchers = defaultdict(list)

            # Override some of our properties with the values we were called with
            stack.enter_context(
                overridden(
                    self,
                    log=log,
                    driver=driver,
                    manager=manager,
                    register_uav=register_uav,
                    _matchers=matchers,
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

    def enqueue_rc_override_packet(self, channels: list[int]) -> None:
        """Handles a list of a RC channels that the server wishes to forward
        to the drones as RC override.

        Parameters:
            channels: the values of the RC channels to send in a MAVLink
                `RC_CHANNELS_OVERRIDE` message
        """
        message = spec.rc_channels_override(
            target_system=0,
            target_component=0,
            chan1_raw=channels[0],
            chan2_raw=channels[1],
            chan3_raw=channels[2],
            chan4_raw=channels[3],
            chan5_raw=channels[4],
            chan6_raw=channels[5],
            chan7_raw=channels[6],
            chan8_raw=channels[7],
            chan9_raw=channels[8],
            chan10_raw=channels[9],
            chan11_raw=channels[10],
            chan12_raw=channels[11],
            chan13_raw=channels[12],
            chan14_raw=channels[13],
            chan15_raw=channels[14],
            chan16_raw=channels[15],
            chan17_raw=channels[16],
            chan18_raw=channels[17],
        )
        self.manager.enqueue_broadcast_packet(
            message, destination=Channel.RC, allow_failure=True
        )

    def enqueue_rtk_correction_packet(self, packet: bytes) -> None:
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in this network.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                this network
        """
        if not self.manager:
            return

        # Do not send the RTK correction packet if the network has no drones yet
        if self.num_uavs == 0:
            return

        for message in self._rtk_correction_packet_encoder.encode(packet):
            self.manager.enqueue_broadcast_packet(
                message, destination=Channel.RTK, allow_failure=True
            )

    def notify_led_light_config_changed(self, config: LightConfiguration):
        """Notifies the network that the LED light configuration of the drones
        has changed in the system. The network will then update the LED light
        configuration of each drone.
        """
        self._led_light_configuration_manager.notify_config_changed(config)

    def notify_scheduled_takeoff_config_changed(self, config: DroneShowConfiguration):
        """Notifies the network that the automatic start configuration of the
        drones has changed in the system. The network will then update the
        start configuration of each drone.
        """
        self._scheduled_takeoff_manager.notify_config_changed(config)

    def notify_show_clock_start_time_changed(self, start_time: Optional[float]) -> None:
        """Notifies the network that the show clock was started, stopped or
        adjusted in the system.
        """
        self._scheduled_takeoff_manager.notify_start_time_changed(start_time)

    @property
    def num_uavs(self) -> int:
        """Returns the number of UAVs in this network."""
        return len(self._uavs)

    @property
    def uav_system_id_offset(self) -> int:
        return self._uav_system_id_offset

    @property
    def uav_system_id_range(self) -> tuple[int, int]:
        return self._uav_system_id_range

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
        spec: Optional[MAVLinkMessageSpecification],
        target: MAVLinkUAV,
        *,
        wait_for_response: Optional[tuple[str, MAVLinkMessageMatcher]] = None,
        wait_for_one_of: Optional[dict[str, MAVLinkMessageSpecification]] = None,
        channel: Optional[str] = None,
    ) -> Union[None, MAVLinkMessage, tuple[str, MAVLinkMessage]]:
        """Sends a message to the given UAV and optionally waits for a matching
        response.

        It is assumed (and not checked) that the UAV belongs to this network.

        Parameters:
            spec: the specification of the MAVLink message to send; ``None`` if
                no packet needs to be sent and we only need to wait for a reply
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

        Returns:
            ``None`` if `wait_for_response` and `wait_for_one_of` are both
            ``None``; the received response if `wait_for_response` was not
            ``None``; the key of the matched message specification and the
            message itself if `wait_for_one_of` was not ``None``.
        """
        tasks: dict[str, Callable[[], Awaitable[MAVLinkMessage]]]

        address = self._uav_addresses.get(target)
        if address is None:
            raise RuntimeError("UAV has no address in this network")

        destination = (channel or Channel.PRIMARY, address)

        if not spec:
            # No sending, only waiting for a reply
            if wait_for_response:
                response_type, response_fields = wait_for_response
                with self.expect_packet(
                    response_type, response_fields, system_id=target.system_id
                ) as future:
                    return await future.wait()

            elif wait_for_one_of:
                tasks = {}

                with ExitStack() as stack:
                    # Prepare futures for every single message type that we expect
                    for key, (
                        response_type,
                        response_fields,
                    ) in wait_for_one_of.items():
                        future = stack.enter_context(
                            self.expect_packet(
                                response_type,
                                response_fields,
                                system_id=target.system_id,
                            )
                        )
                        tasks[key] = future.wait

                    return await race(tasks)

            else:
                # Nothing to do as we don't send and don't expect anything
                return

        # From this point onwards, spec is not None, i.e. we are definitely
        # sending something

        spec[1].update(
            target_system=target.system_id,
            target_component=MAVComponent.AUTOPILOT1,
            _mavlink_version=target.mavlink_version,
        )

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

    def _create_uav(self, system_id: int) -> MAVLinkUAV:
        """Creates a new UAV with the given system ID in this network and
        registers it in the UAV registry.
        """
        lo, hi = self._uav_system_id_range
        if system_id < lo or system_id >= hi:
            raise InvalidSystemIdError(
                system_id,
                f"System ID must be between {lo} and {hi - 1}, got {system_id}",
            )

        uav_id = self._id_formatter(system_id + self._uav_system_id_offset, self.id)

        self._uavs[system_id] = uav = self.driver.create_uav(uav_id)
        uav.assign_to_network_and_system_id(self.id, system_id)
        uav._rssi_mode = self._rssi_mode

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
            message was a broadcast message or belonged to a system ID that is
            outside the range configured for this network
        """
        system_id: int = message.get_srcSystem()
        if system_id == 0:
            return None
        else:
            uav = self._uavs.get(system_id)
            if not uav:
                try:
                    uav = self._create_uav(system_id)
                except InvalidSystemIdError:
                    return None

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

    async def _handle_inbound_messages(
        self, channel: ReceiveChannel[tuple[str, tuple[MAVLinkMessage, Any]]]
    ):
        """Handles inbound MAVLink messages from all the communication links
        that the extension manages.

        Parameters:
            channel: a Trio receive channel that yields inbound MAVLink messages.
        """
        # We need to respond to RADIO_STATUS messages only if we are parsing the
        # RSSI values from there, otherwise they can be ignored
        radio_status_handler = (
            self._handle_message_radio_status
            if self._rssi_mode is RSSIMode.RADIO_STATUS
            else nop
        )

        handlers = {
            "AUTOPILOT_VERSION": self._handle_message_autopilot_version,
            "BAD_DATA": nop,
            "COMMAND_ACK": nop,
            "COMMAND_LONG": self._handle_message_command_long,
            "DATA16": self._handle_message_data,
            "DATA32": self._handle_message_data,
            "DATA64": self._handle_message_data,
            "DATA96": self._handle_message_data,
            "FENCE_STATUS": nop,
            "FILE_TRANSFER_PROTOCOL": nop,
            "GLOBAL_POSITION_INT": self._handle_message_global_position_int,
            "GPS_GLOBAL_ORIGIN": nop,
            "GPS_RAW_INT": self._handle_message_gps_raw_int,
            "HEARTBEAT": self._handle_message_heartbeat,
            "HOME_POSITION": nop,
            "HWSTATUS": nop,
            "LOCAL_POSITION_NED": nop,  # maybe later?
            "LOG_DATA": self._handle_message_log_data,
            "LOG_ENTRY": self._handle_message_log_entry,
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
            "RADIO_STATUS": radio_status_handler,
            "STATUSTEXT": self._handle_message_statustext,
            "SYS_STATUS": self._handle_message_sys_status,
            "TIMESYNC": self._handle_message_timesync,
            "V2_EXTENSION": self._handle_message_v2_extension,
        }

        autopilot_component_id = MAVComponent.AUTOPILOT1
        udp_bridge_id = MAVComponent.UDP_BRIDGE

        # Many third-party MAVLink-based drones do not respond to broadcast
        # messages sent to them with an IP address of 255.255.255.255 as they
        # listen to the subnet-specific broadcast address only (e.g., 192.168.0.255).
        # Therefore, we need to re-bind the broadcast address of the channel
        # as soon as we have received the first packet from it, based on the
        # address of that packet and the netmasks of the network interfaces.
        # The following dict stores whether a link was already switched to its
        # subnet-specific broadcast address
        broadcast_address_updated: dict[str, bool] = defaultdict(bool)

        async for connection_id, (message, address) in channel:
            # Uncomment this for debugging
            # self.log.info(repr(message))

            # SiK radios use system ID = 51 and component ID = 68
            # (MAV_COMP_ID_TELEMETRY_RADIO)
            # mavesp8266 uses the correct system ID and component ID = 0xf0
            # (MAV_COMP_ID_UDP_BRIDGE)

            # Get the source component and the message type
            src_component = message.get_srcComponent()
            type = message.get_type()

            # Determine whether we should process this message
            should_process = src_component == autopilot_component_id or (
                src_component == udp_bridge_id and type == "RADIO_STATUS"
            )
            if not should_process:
                continue

            # Update the broadcast address to a subnet-specific one if needed
            if not broadcast_address_updated[connection_id]:
                await self._update_broadcast_address_of_channel_to_subnet(
                    connection_id, address
                )
                broadcast_address_updated[connection_id] = True

            # Resolve all futures that are waiting for this message
            for system_id, params, future in self._matchers[type]:
                # Check system ID early on and skip if it does not match
                if system_id is not None and message.get_srcSystem() != system_id:
                    continue

                if future.done():
                    # This may happen if we get multiple matching messages in
                    # quick succession before the task waiting for the result
                    # gets a chance of responding to them; in this case, we
                    # have to ignore the message, otherwise we would be resolving
                    # the future twice
                    continue
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
                self.log.warning(
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

    def _handle_message_command_long(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink COMMAND_LONG message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_command_long(message)

    def _handle_message_data(
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

    def _handle_message_log_data(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink LOG_DATA message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_log_data(message)

    def _handle_message_log_entry(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink LOG_ENTRY message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_log_entry(message)

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

    def _handle_message_radio_status(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink RADIO_STATUS message."""
        uav = self._find_uav_from_message(message, address)
        if uav:
            uav.handle_message_radio_status(message)

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
            if not self._statustext_targets.log_prearm:
                return

        severity: int = message.severity
        if severity <= self._statustext_targets.server:
            extra = self._log_extra_from_message(message, uav)
            extra["telemetry"] = "ignore"
            self.log.log(
                python_log_level_from_mavlink_severity(message.severity),
                text,
                extra=extra,
            )
        if severity <= self._statustext_targets.client and uav is not None:
            uav.send_log_message_to_gcs(
                text,
                severity=flockwave_severity_from_mavlink_severity(message.severity),
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

    def _log_extra_from_message(
        self, message: MAVLinkMessage, uav: MAVLinkUAV | None = None
    ) -> dict[str, Any]:
        if uav:
            return {"id": log_id_for_uav(uav)}
        else:
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

        def register_by_index(
            alias: str, index: Optional[Union[int, Iterable[int]]]
        ) -> list[str]:
            if index is None:
                index = 0

            indices = [index] if isinstance(index, int) else list(index)

            # If the list contains a channel that does not exist, replace it
            # with channel 0 instead. Also filter duplicates.
            targets = [
                connection_names[index]
                for index in sorted(
                    {
                        index if index >= 0 and index < len(connection_names) else 0
                        for index in indices
                    }
                )
            ]
            stack.enter_context(manager.with_alias(alias, targets=targets))

            return targets

        extra = {"id": self.id}

        # Register the first connection as the primary
        channels = register_by_index(Channel.PRIMARY, 0)
        secondary_channels = register_by_index(Channel.SECONDARY, 1)
        if channels != secondary_channels:
            log.info(
                f"Routing primary traffic to {format_channel_ids(channels)}, "
                f"falling back to {format_channel_ids(secondary_channels)}",
                extra=extra,
            )
        else:
            log.info(
                f"Routing primary traffic to {format_channel_ids(channels)}",
                extra=extra,
            )

        # Register the RTK channel according to the routing setup
        channels = format_channel_ids(
            register_by_index(Channel.RTK, self._routing.get("rtk"))
        )
        if channels:
            log.info(f"Routing RTK corrections to {channels}", extra=extra)

        # Register the RTK channel according to the routing setup
        channels = format_channel_ids(
            register_by_index(Channel.RC, self._routing.get("rc"))
        )
        if channels:
            log.info(f"Routing RC overrides to {channels}", extra=extra)

    async def _update_broadcast_address_of_channel_to_subnet(
        self, connection_id: str, address: tuple[str, int], timeout: float = 1
    ) -> None:
        """Updates the broadcast address of the connection with the given ID to the
        subnet-specific broadcast address of the network interface that received
        a packet from the given address.
        """
        if isinstance(address, tuple):
            ip, port = address
        else:
            # Not a wireless network
            return

        subnets = None
        with move_on_after(timeout):
            subnets = await to_thread.run_sync(find_interfaces_with_address, ip)

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

                        # Try to figure out the current broadcast address of the channel
                        # TODO(ntamas): this should be sorted out in the future
                        old_broadcast_address = getattr(
                            entry.channel, "broadcast_address", None
                        )
                        if old_broadcast_address is None:
                            connection = getattr(entry, "connection", None)
                            if connection:
                                old_broadcast_address = getattr(
                                    connection, "broadcast_address", None
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

                        conn = entry.connection
                        if isinstance(conn, UDPListenerConnection):
                            conn.set_user_defined_broadcast_address(
                                (
                                    str(broadcast_address),
                                    broadcast_port,
                                )
                            )
                            self.log.info(
                                f"Broadcast address updated to {broadcast_address}:{broadcast_port} "
                                f"({interface})",
                                extra={"id": connection_id},
                            )
                            success = True

        if not success:
            self.log.warning(
                "Failed to update broadcast address to a subnet-specific one"
            )


def format_channel_ids(ids: Sequence[str]) -> str:
    """Formats a list of communication channel IDs in a way that is suitable for
    printing in human-readable logs.
    """
    parts: list[str] = []
    for index, id in enumerate(ids):
        parts.append(id)
        if index < len(ids) - 1:
            parts.append(", " if index < len(ids) - 2 else " and ")
    return "".join(parts)
