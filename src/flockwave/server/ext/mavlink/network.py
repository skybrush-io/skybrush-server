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
from time import time_ns
from trio.abc import ReceiveChannel
from typing import Any, Callable, Dict, Optional, Union

from flockwave.connections import Connection, create_connection
from flockwave.server.concurrency import Future
from flockwave.server.model import ConnectionPurpose, UAV
from flockwave.server.utils import nop, overridden

from .comm import create_communication_manager, MAVLinkMessage
from .driver import MAVLinkUAV
from .enums import MAVComponent, MAVMessageType, MAVType
from .types import MAVLinkMessageSpecification, MAVLinkNetworkSpecification
from .utils import log_level_from_severity, log_id_from_message

__all__ = ("MAVLinkNetwork",)

DEFAULT_NAME = ""


class MAVLinkNetwork:
    """Representation of a MAVLink network."""

    @classmethod
    def from_specification(cls, spec: MAVLinkNetworkSpecification):
        """Creates a MAVLink network from its specification, typically found in
        a configuration file.
        """
        result = cls(
            spec.id, system_id=spec.system_id, id_formatter=spec.id_format.format
        )

        for index, connection_spec in enumerate(spec.connections):
            connection = create_connection(connection_spec)
            result.add_connection(connection)

        return result

    def __init__(
        self,
        id: str,
        *,
        system_id: int = 255,
        id_formatter: Callable[[int, str], str] = "{1}{0}".format,
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
        """
        self._id = id
        self._id_formatter = id_formatter
        self._matchers = None
        self._system_id = 255

        self._connections = []
        self._uavs = {}

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
        params: Optional[Dict[str, Any]] = None,
    ) -> Future[MAVLinkMessage]:
        """Sets up a handler that waits for a MAVLink packet of a given type,
        optionally matching its content with the given parameter values based
        on strict equality.

        Parameters:
            type: the type of the MAVLink message to wait for
            params: dictionary mapping parameter names to the values that we
                expect to see in the matched packet

        Returns:
            a Future that resolves to the next MAVLink message that matches the
            given type and parameter values.
        """
        type_str = type if isinstance(type, str) else MAVMessageType(type).name

        future = Future()
        item = (params, future)
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

    async def run(self, *, driver, log, register_uav, supervisor, use_connection):
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
        with ExitStack() as stack:
            # Register the communication links
            if len(self._connections) > 1:
                id_format = "{0}/{1}"
            else:
                id_format = "{0}"

            for index, connection in enumerate(self._connections):
                full_id = id_format.format(self.id, index)
                stack.enter_context(
                    use_connection(
                        connection,
                        f"MAVLink: {full_id}",
                        description=f"Upstream MAVLink connection ({full_id})",
                        purpose=ConnectionPurpose.uavRadioLink,
                    )
                )

            # Create the communication manager
            manager = create_communication_manager()

            # Register the links with the communication manager. The order is
            # important here; the ones coming first will primarily be used for
            # sending, falling back to later ones if sending on the first one
            # fails
            for index, connection in enumerate(self._connections):
                manager.add(connection, name=DEFAULT_NAME)

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
                )
            )

            # Start the communication manager
            try:
                await manager.run(
                    consumer=self._handle_inbound_messages,
                    supervisor=supervisor,
                    log=log,
                )
            finally:
                for matcher in matchers.values():
                    for _, future in matcher:
                        future.cancel()

    async def send_packet(
        self,
        spec: MAVLinkMessageSpecification,
        target: UAV,
        wait_for_response: Optional[MAVLinkMessageSpecification] = None,
    ) -> Optional[MAVLinkMessage]:
        """Sends a message to the given UAV and optionally waits for a matching
        response.

        It is assumed (and not checked) that the UAV belongs to this network.

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
        spec[1].update(
            target_system=target.system_id, target_component=MAVComponent.AUTOPILOT1
        )

        if wait_for_response:
            response_type, response_fields = wait_for_response
            with self.expect_packet(response_type, response_fields) as future:
                # TODO(ntamas): in theory, we could be getting a matching packet
                # _before_ we sent ours. Sort this out if it causes problems.
                await self.manager.send_packet(spec, (DEFAULT_NAME, None))
                return await future.wait()
        else:
            await self.manager.send_packet(spec, (DEFAULT_NAME, None))

    def _create_uav(self, system_id: str) -> MAVLinkUAV:
        """Creates a new UAV with the given system ID in this network and
        registers it in the UAV registry.
        """
        uav_id = self._id_formatter(system_id, self.id)

        self._uavs[system_id] = uav = self.driver.create_uav(uav_id)
        uav.assign_to_network_and_system_id(self.id, system_id)

        self.register_uav(uav)

        return uav

    def _find_uav_from_message(self, message: MAVLinkMessage) -> Optional[UAV]:
        """Finds the UAV that this message refers to, creating a new UAV object
        if we have not seen the UAV yet.

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
            return uav

    async def _handle_inbound_messages(self, channel: ReceiveChannel):
        """Handles inbound MAVLink messages from all the communication links
        that the extension manages.

        Parameters:
            channel: a Trio receive channel that yields inbound MAVLink messages.
        """
        handlers = {
            "BAD_DATA": nop,
            "COMMAND_ACK": nop,
            "GPS_RAW_INT": self._handle_message_gps_raw_int,
            "HEARTBEAT": self._handle_message_heartbeat,
            "HWSTATUS": nop,
            "LOCAL_POSITION_NED": nop,  # maybe later?
            "MEMINFO": nop,
            "MISSION_CURRENT": nop,  # maybe later?
            "NAV_CONTROLLER_OUTPUT": nop,
            "PARAM_VALUE": self._handle_message_param_value,
            "POWER_STATUS": nop,
            "STATUSTEXT": self._handle_message_statustext,
            "SYS_STATUS": self._handle_message_sys_status,
            "TIMESYNC": self._handle_message_timesync,
        }

        autopilot_component_id = MAVComponent.AUTOPILOT1

        async for connection_id, (message, address) in channel:
            if message.get_srcComponent() != autopilot_component_id:
                # We do not handle messages from any other component but an
                # autopilot
                continue

            # Get the message type
            type = message.get_type()

            # Resolve all futures that are waiting for this message
            for params, future in self._matchers[type]:
                # TODO(ntamas): match params
                future.set_result(message)

            # Call the message handler if we have one
            handler = handlers.get(type)
            if handler:
                handler(message, connection_id=connection_id, address=address)
            else:
                self.log.warn(
                    f"Unhandled MAVLink message type: {type}",
                    extra=self._log_extra_from_message(message),
                )
                handlers[type] = nop

    def _handle_message_gps_raw_int(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message)
        if uav:
            uav.handle_message_gps_raw_int(message)

    def _handle_message_heartbeat(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink HEARTBEAT message."""
        if not MAVType(message.type).is_vehicle:
            # Ignore non-vehicle heartbeats
            return

        uav = self._find_uav_from_message(message)
        if uav:
            uav.handle_message_heartbeat(message)

        # Send a timesync message for testing purposes
        # spec = ("TIMESYNC", {"tc1": 0, "ts1": time_ns() // 1000})
        # self._manager.enqueue_packet(spec, (connection_id, network_id))

    def _handle_message_param_value(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink PARAM_VALUE message."""
        self.log.info(
            f"{message.param_id!r} = {message.param_value}",
            extra=self._log_extra_from_message(message),
        )

    def _handle_message_statustext(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        """Handles an incoming MAVLink STATUSTEXT message and forwards it to the
        log console.
        """
        self.log.log(
            log_level_from_severity(message.severity),
            message.text,
            extra=self._log_extra_from_message(message),
        )

    def _handle_message_sys_status(
        self, message: MAVLinkMessage, *, connection_id: str, address: Any
    ):
        uav = self._find_uav_from_message(message)
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

    def _log_extra_from_message(self, message: MAVLinkMessage):
        return {"id": log_id_from_message(message, self.id)}
