"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from math import inf
from time import monotonic
from trio import move_on_after
from typing import Optional

from flockwave.gps.vectors import GPSCoordinate, VelocityNED

from flockwave.server.errors import NotSupportedError
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.gps import GPSFix
from flockwave.server.model.uav import UAVBase, UAVDriver

from .autopilots import Autopilot, UnknownAutopilot
from .enums import GPSFixType, MAVCommand, MAVDataStream, MAVResult
from .types import MAVLinkMessage, spec

__all__ = ("MAVLinkDriver",)


#: Conversion constant from seconds to microseconds
SEC_TO_USEC = 1000000


class MAVLinkDriver(UAVDriver):
    """Driver class for MAVLink-based drones.

    Attributes:
        app (SkybrushServer): the app in which the driver lives
        create_device_tree_mutator (callable): a function that should be
            called by the driver as a context manager whenever it wants to
            mutate the state of the device tree
        send_packet (callable): a function that should be called by the
            driver whenever it wants to send a packet. The function must
            be called with the packet to send, and a pair formed by the
            medium via which the packet should be forwarded and the
            destination address in that medium.
    """

    def __init__(self, app=None):
        """Constructor.

        Parameters:
            app: the app in which the driver lives
        """
        super().__init__()

        self.app = app

        self.create_device_tree_mutator = None
        self.log = None
        self.run_in_background = None
        self.send_packet = None

    def create_uav(self, id: str) -> "MAVLinkUAV":
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id: the identifier of the UAV to create

        Returns:
            MAVLinkUAV: an appropriate MAVLink UAV object
        """
        uav = MAVLinkUAV(id, driver=self)
        uav.notify_updated = partial(self.app.request_to_send_UAV_INF_message_for, [id])
        return uav

    async def send_command_long(
        self,
        target: "MAVLinkUAV",
        command_id: int,
        param1: float = 0,
        param2: float = 0,
        param3: float = 0,
        param4: float = 0,
        param5: float = 0,
        param6: float = 0,
        param7: float = 0,
        *,
        confirmation: int = 0,
    ) -> Optional[MAVLinkMessage]:
        """Sends a MAVLink command to a given UAV and waits for an acknowlegment.

        Parameters:
            target: the UAV to send the command to
            param1: the first parameter of the command
            param2: the second parameter of the command
            param3: the third parameter of the command
            param4: the fourth parameter of the command
            param5: the fifth parameter of the command
            param6: the sixth parameter of the command
            param7: the seventh parameter of the command
            confirmation: confirmation count for commands that require a
                confirmation

        Returns:
            whether the command was executed successfully

        Raises:
            NotSupportedError: if the command is not supported by the UAV
        """
        response = await self.send_packet(
            (
                "COMMAND_LONG",
                {
                    "command": command_id,
                    "param1": param1,
                    "param2": param2,
                    "param3": param3,
                    "param4": param4,
                    "param5": param5,
                    "param6": param6,
                    "param7": param7,
                    "confirmation": confirmation,
                },
            ),
            target,
            wait_for_response=("COMMAND_ACK", {"command": command_id}),
        )
        result = response.result

        if result == MAVResult.UNSUPPORTED:
            raise NotSupportedError

        return result == MAVResult.ACCEPTED

    async def _send_reset_signal_single(self, uav, component):
        if not component:
            # Resetting the whole UAV, this is supported
            result = await self.send_command_long(
                uav, MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN, 1  # reboot autopilot
            )
            return bool(result)
        else:
            # No component resets are implemented on this UAV yet
            return False

    async def _send_shutdown_signal_single(self, uav):
        result = await self.send_command_long(
            uav, MAVCommand.PREFLIGHT_REBOOT_SHUTDOWN, 2  # shutdown autopilot
        )
        return bool(result)


@dataclass
class MAVLinkMessageRecord:
    """Simple object holding a pair of a MAVLink message and the corresponding
    monotonic timestamp when the message was observed.
    """

    message: MAVLinkMessage = None
    timestamp: float = None

    @property
    def age(self) -> float:
        """Returns the number of seconds elapsed since the record was updated
        the last time.
        """
        return monotonic() - self.timestamp

    def update(self, message: MAVLinkMessage) -> None:
        """Updates the record with a new MAVLink message."""
        self.message = message
        self.timestamp = monotonic()


class MAVLinkUAV(UAVBase):
    """Subclass for UAVs created by the driver for MAVLink-based drones.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

        self._autopilot = UnknownAutopilot
        self._battery = BatteryInfo()
        self._gps_fix = GPSFix()
        self._is_connected = False
        self._last_messages = defaultdict(MAVLinkMessageRecord)
        self._network_id = None
        self._position = GPSCoordinate()
        self._velocity = VelocityNED()
        self._system_id = None

        self.notify_updated = None

    def assign_to_network_and_system_id(self, network_id: str, system_id: int) -> None:
        """Assigns the UAV to the MAVLink network with the given network ID.
        The UAV is assumed to have the given system ID in the given network, and
        it is assumed to have a component ID of 1 (primary autopilot). We are
        not talking to any other component of a MAVLink system yet.
        """
        if self._network_id is not None:
            raise RuntimeError(
                f"This UAV is already a member of MAVLink network {self._network_id}"
            )

        self._network_id = network_id
        self._system_id = system_id

    def handle_message_heartbeat(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        self._store_message(message)

        if not self._is_connected:
            self._autopilot = Autopilot.from_heartbeat(message)
            self.notify_reconnection()

        self.update_status(
            mode=self._autopilot.describe_mode(message.base_mode, message.custom_mode)
        )
        self.notify_updated()

    def handle_message_global_position_int(self, message: MAVLinkMessage):
        # TODO(ntamas): reboot detection with time_boot_ms

        self._position.lat = message.lat / 1e7
        self._position.lon = message.lon / 1e7
        self._position.amsl = message.alt / 1e3
        self._position.agl = message.relative_alt / 1e3

        self._velocity.x = message.vx / 100
        self._velocity.y = message.vy / 100
        self._velocity.z = message.vz / 100

        self.update_status(
            position=self._position, velocity=self._velocity, heading=message.hdg / 10
        )
        self.notify_updated()

    def handle_message_gps_raw_int(self, message: MAVLinkMessage):
        num_sats = message.satellites_visible
        self._gps_fix.type = GPSFixType(message.fix_type).to_ours()
        self._gps_fix.num_satellites = (
            num_sats if num_sats < 255 else None
        )  # 255 = unknown
        self.update_status(gps=self._gps_fix)
        self.notify_updated()

    def handle_message_sys_status(self, message: MAVLinkMessage):
        # TODO(ntamas): check sensor health, update flags accordingly
        self._battery.voltage = message.voltage_battery / 1000
        self._battery.percentage = message.battery_remaining
        self.update_status(battery=self._battery)
        self.notify_updated()

    def handle_message_system_time(self, message: MAVLinkMessage):
        """Handles an incoming MAVLink HEARTBEAT message targeted at this UAV."""
        previous_message = self._get_previous_copy_of_message(message)
        if previous_message:
            # TODO(ntamas): compare the time since boot with the previous
            # version to detect reboot events
            pass

        self._store_message(message)

    @property
    def is_connected(self) -> bool:
        """Returns whether the UAV is connected to the network."""
        return self._is_connected

    @property
    def network_id(self) -> str:
        """The network ID Of the UAV."""
        return self._network_id

    @property
    def system_id(self) -> str:
        """The system ID Of the UAV."""
        return self._system_id

    def notify_disconnection(self) -> None:
        """Notifies the UAV state object that we have detected that it has been
        disconnected from the network.
        """
        self._is_connected = False
        # TODO(ntamas): trigger a warning flag in the UAV?

    def notify_reconnection(self) -> None:
        """Notifies the UAV state object that it has been reconnected to the
        network.
        """
        self._is_connected = True
        # TODO(ntamas): clear a warning flag in the UAV?

        if self._was_probably_rebooted_after_reconnection():
            self._handle_reboot()

    async def _configure_data_streams(self) -> None:
        """Configures the data streams that we want to receive from the UAV."""
        # We give ourselves 5 seconds to configure everything
        with move_on_after(5):
            # TODO(ntamas): this is unsafe; there are no confirmations for
            # REQUEST_DAYA_STREAM commands so we never know if we succeeded or
            # not
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=0, req_message_rate=0, start_stop=0
                ),
                target=self,
            )

            # EXTENDED_STATUS: we need SYS_STATUS from it for the general status
            # flags and GPS_RAW_INT for the GPS fix info.
            # We might also need MISSION_CURRENT.
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=MAVDataStream.EXTENDED_STATUS,
                    req_message_rate=1,
                    start_stop=1,
                ),
                target=self,
            )

            # POSITION: we need GLOBAL_POSITION_INT for position and velocity
            await self.driver.send_packet(
                spec.request_data_stream(
                    req_stream_id=MAVDataStream.POSITION,
                    req_message_rate=2,
                    start_stop=1,
                ),
                target=self,
            )

    def _handle_reboot(self) -> None:
        """Handles a reboot event on the autopilot and attempts to re-initialize
        the data streams.
        """
        self.driver.run_in_background(self._configure_data_streams)

    def get_age_of_message(self, type: int, now: Optional[float] = None) -> float:
        """Returns the number of seconds elapsed since we have last seen a
        message of the given type.
        """
        record = self._last_messages.get(int(type))
        if now is None:
            now = monotonic()
        return record.timestamp - now if record else inf

    def get_last_message(self, type: int) -> Optional[MAVLinkMessage]:
        """Returns the last MAVLink message that was observed with the given
        type or `None` if we have not observed such a message yet.
        """
        record = self._last_messages.get(int(type))
        return record.message if record else None

    def _get_previous_copy_of_message(
        self, message: MAVLinkMessage
    ) -> Optional[MAVLinkMessage]:
        """Returns the previous copy of this MAVLink message, or `None` if we
        have not observed such a message yet.
        """
        record = self._get_previous_record_of_message(message)
        return record.message if record else None

    def _get_previous_record_of_message(
        self, message: MAVLinkMessage
    ) -> Optional[MAVLinkMessageRecord]:
        """Returns the previous copy of this MAVLink message and its timestamp,
        or `None` if we have not observed such a message yet.
        """
        return self._last_messages.get(message.get_msgId())

    def _store_message(self, message: MAVLinkMessage) -> None:
        """Stores the given MAVLink message in the dictionary that maps
        MAVLink message types to their most recent versions that were seen
        for this UAV.
        """
        self._last_messages[message.get_msgId()].update(message)

    def _was_probably_rebooted_after_reconnection(self) -> bool:
        """Returns whether the UAV was probably rebooted recently, _assuming_
        that a reconnection event happened.

        This function _must_ be called only after a reconnection event. Right
        now we always return `True`, but we could implement a more sophisticated
        check in the future based on the `SYSTEM_TIME` messages and whether the
        `time_boot_ms` timestamp has decreased.
        """
        return True
