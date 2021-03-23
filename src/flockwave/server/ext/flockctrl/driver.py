"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from base64 import b64decode
from bidict import bidict
from colour import Color
from time import monotonic
from trio import CapacityLimiter, to_thread
from typing import Optional, List, Tuple
from zlib import decompress

from flockwave.concurrency import FutureCancelled, FutureMap
from flockwave.protocols.flockctrl.enums import (
    MultiTargetCommand,
    StatusFlag,
    PrearmCheck,
)
from flockwave.protocols.flockctrl.packets import (
    AlgorithmDataPacket,
    ChunkedPacketAssembler,
    CommandRequestPacket,
    CommandResponsePacketBase,
    CommandResponsePacket,
    CompressedCommandResponsePacket,
    MissionInfoPacket,
    MultiTargetCommandPacket,
    PrearmStatusPacket,
    RawGPSInjectionPacket,
    StatusPacket,
)
from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.logger import log
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.gps import GPSFixType
from flockwave.server.model.preflight import PreflightCheckInfo, PreflightCheckResult
from flockwave.server.model.transport import TransportOptions
from flockwave.server.model.uav import UAVBase, UAVDriver, VersionInfo
from flockwave.server.utils import color_to_rgb565, nop
from flockwave.spec.ids import make_valid_object_id

from .algorithms import handle_algorithm_data_packet
from .comm import BurstedMultiTargetMessageManager, upload_mission
from .errors import AddressConflictError, map_flockctrl_error_code_and_flags

from .mission import generate_mission_file_from_show_specification

__all__ = ("FlockCtrlDriver",)

MAX_GEIGER_TUBE_COUNT = 2
MAX_CAMERA_FEATURE_COUNT = 32

#: Type specification for UAV network addresses in this driver
UAVAddress = Tuple[str, int]

#: Default duration of bursted command packets, in seconds
BURST_DURATION = 5


class FlockCtrlDriver(UAVDriver):
    """Driver class for FlockCtrl-based drones.

    Attributes:
        allow_multiple_commands_per_uav (bool): whether the driver should
            allow the user to send a command to an UAV while another one is
            still in progress (i.e. hasn't timed out). When the property is
            ``True``, sending the second command is allowed and it will
            automatically cancel the first command. When the property is
            ``False``, sending the second command is not allowed until the
            user cancels the execution of the first command explicitly.
            The default is ``True``.
        app (SkybrushServer): the app in which the driver lives
        id_format (str): Python format string that receives a numeric
            drone ID in the flock and returns its preferred formatted
            identifier that is used when the drone is registered in the
            server, or any other object that has a ``format()`` method
            accepting a single integer as an argument and returning the
            preferred UAV identifier.
        create_device_tree_mutator (callable): a function that should be
            called by the driver as a context manager whenever it wants to
            mutate the state of the device tree
        send_packet (callable): a function that should be called by the
            driver whenever it wants to send a packet. The function must
            be called with the packet to send, and a pair formed by the
            medium via which the packet should be forwarded and the
            destination address in that medium.
        broadcast_packet (callable): a function that should be called by the
            driver whenever it wants to broadcast a packet. The function must
            be called with the packet to send and the name of the medium via
            which the packet should be forwarded.
    """

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (SkybrushServer): the app in which the driver lives
            id_format (str): the format of the UAV IDs used by this driver.
                See the class documentation for more details.
        """
        super().__init__()

        self._pending_commands_by_uav = FutureMap()

        self._bursted_message_manager = BurstedMultiTargetMessageManager(self)
        self._disable_warnings_until = {}
        self._index_to_uav_id = bidict()
        self._uavs_by_source_address = {}
        self._upload_capacity = CapacityLimiter(5)

        # These functions will be provided to the driver by the extension
        self.broadcast_packet = None
        self.create_device_tree_mutator = None
        self.run_in_background = None
        self.send_packet = None

        self._packet_handlers = self._configure_packet_handlers()
        self._packet_assembler = ChunkedPacketAssembler()

        self.allow_multiple_commands_per_uav = True
        self.app = app
        self.id_format = id_format
        self.log = log.getChild("flockctrl").getChild("driver")

    async def handle_command___mission_upload(self, uav: "FlockCtrlUAV", mission: str):
        """Handles a mission upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            mission: the mission file, in ZIP format, encoded as base64
        """
        # prevent mission uploads if the drone is airborne
        if uav.is_airborne:
            raise RuntimeError("Cannot upload a mission while the drone is airborne")

        await self._handle_mission_upload(uav, b64decode(mission))

    async def handle_command___show_upload(self, uav: "FlockCtrlUAV", *, show):
        """Handles a drone show upload request for the given UAV.

        This is a temporary solution until we figure out something that is
        more sustainable in the long run.

        Parameters:
            show: the show data
        """
        # prevent show uploads if the drone is airborne
        if uav.is_airborne:
            raise RuntimeError("Cannot upload a show while the drone is airborne")

        mission = generate_mission_file_from_show_specification(show)
        await self._handle_mission_upload(uav, mission)

    async def handle_command_calib(self, uav, component: Optional[str] = None) -> None:
        """Calibrates a component of the UAV."""
        if component == "baro":
            return await uav.calibrate_component("baro")
        elif component == "compass":
            return await uav.calibrate_component("compass")
        elif component == "gyro":
            return await uav.calibrate_component("gyro")
        elif component == "level":
            return await uav.calibrate_component("level")
        elif not component:
            return "Usage: calib <baro|compass|gyro|level>"
        else:
            raise NotSupportedError

    async def handle_generic_command(self, uav, command, args, kwds):
        """Sends a generic command execution request to the given UAV."""
        command = " ".join([command, *args])
        response = await self._send_command_to_uav_and_check_for_errors(command, uav)
        return {"type": "preformatted", "data": response}

    def handle_inbound_packet(self, packet, source):
        """Handles an inbound FlockCtrl packet received over a connection."""
        packet_class = packet.__class__
        handler = self._packet_handlers.get(packet_class)
        if handler is None:
            self.log.warn(
                "No packet handler defined for packet "
                "class: {0}".format(packet_class.__name__)
            )
            return

        try:
            handler(packet, source)
        except AddressConflictError as ex:
            uav_id = ex.uav.id if ex.uav else None
            deadline = self._disable_warnings_until.get(uav_id, 0)
            now = monotonic()
            if now >= deadline:
                self.log.warn(
                    "Dropped packet from invalid source: "
                    "{0}/{1}, sent to UAV {2}".format(ex.medium, ex.address, uav_id)
                )
                self._disable_warnings_until[uav_id] = now + 1

    def send_landing_signal(self, uavs, transport: Optional[TransportOptions] = None):
        self._bursted_message_manager.schedule_burst(
            MultiTargetCommand.LAND,
            uav_ids=self._uavs_to_ids(uavs),
            duration=BURST_DURATION,
        )

    def send_light_or_sound_emission_signal(
        self,
        uavs,
        signals: List[str],
        duration: int,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a light or sound emission signal to the
        given UAVs.

        Parameters:
            uavs: the UAVs to address with this request.
            signals: the list of signal types that the
                targeted UAVs should emit (e.g., 'sound', 'light')
            duration: the duration of the required signal in seconds
        """
        if "light" not in signals:
            return

        self._bursted_message_manager.schedule_burst(
            MultiTargetCommand.FLASH_LIGHT,
            uav_ids=self._uavs_to_ids(uavs),
            duration=duration,
        )

    def send_motor_start_stop_signal(
        self,
        uavs,
        start: bool = False,
        force: bool = True,
        transport: Optional[TransportOptions] = None,
    ):
        if start:
            command = MultiTargetCommand.MOTOR_ON
        elif force:
            command = MultiTargetCommand.FORCE_MOTOR_OFF
        else:
            command = MultiTargetCommand.MOTOR_OFF
        self._bursted_message_manager.schedule_burst(
            command, uav_ids=self._uavs_to_ids(uavs), duration=BURST_DURATION
        )

    def send_return_to_home_signal(
        self, uavs, transport: Optional[TransportOptions] = None
    ):
        self._bursted_message_manager.schedule_burst(
            MultiTargetCommand.RTH,
            uav_ids=self._uavs_to_ids(uavs),
            duration=BURST_DURATION,
        )

    def send_takeoff_signal(
        self,
        uavs,
        *,
        scheduled: bool = False,
        transport: Optional[TransportOptions] = None,
    ):
        self._bursted_message_manager.schedule_burst(
            MultiTargetCommand.TAKEOFF,
            uav_ids=self._uavs_to_ids(uavs),
            duration=BURST_DURATION,
        )

    def validate_command(self, command: str, args, kwds) -> Optional[str]:
        if command in ("__mission_upload", "__show_upload"):
            # Anything is allowed for our temporary commands
            return

        # Prevent the usage of keyword arguments; they are not supported.
        # Also prevent non-string positional arguments.
        if kwds:
            raise RuntimeError("Keyword arguments not supported")
        if args and any(not isinstance(arg, str) for arg in args):
            raise RuntimeError("Non-string positional arguments not supported")

    def _are_addresses_in_conflict(self, old, new) -> bool:
        """Checks whether two UAV addresses on the same communication medium
        are in conflict or not.

        It is guaranteed that the two addresses are not equal when this function
        is invoked.

        The current implementation works as follows. If the old address and
        the new address are both associated to localhost, the two addresses
        are assumed to be compatible (virtual uavs assumed). If not,
        they are compatible only if their IP match and their ports are in the
        standard ephemeral port range. (It happens a lot that the ports
        from which the UAVs broadcast their messages change over time
        if one of the UAVs is restarted).

        In all other cases the two addresses are deemed incompatible.
        """
        old_ip, old_port = old
        new_ip, new_port = new
        # if IPs do not match, addresses are surely incompatible
        if old_ip != new_ip:
            return True
        # if we are in localhost, we allow different ports
        if old_ip in ("127.0.0.1", "::1"):
            return False
        # if we are in other networks, we allow different ports only if
        # they are both in standard UDP ephemeral port range
        if old_port >= 32768 and new_port >= 32768:
            return False
        # all special cases checked, there is incompatibility
        return True

    def _check_or_record_uav_address(self, uav, medium, address):
        """Records that the given UAV has the given address,
        or, if the UAV already has an address, checks whether the
        address matches the one provided to this function.

        Parameters:
            uav (FlockCtrlUAV): the UAV to check
            medium (str): the communication medium on which the address is
                valid (e.g., ``wireless``)
            address (object): the source address of the UAV

        Raises:
            AddressConflictError: if the UAV already has an address and it
                is not compatible with the one given to this function
                according to the current address conflict policy of the
                driver
        """
        existing_address = uav.addresses.get(medium)
        if existing_address == address:
            return
        elif existing_address is not None:
            if self._are_addresses_in_conflict(existing_address, address):
                raise AddressConflictError(uav, medium, address)

        if existing_address is not None:
            self.log.warn(
                "UAV possibly rebooted, address changed to {}".format(address)
            )
        uav.addresses[medium] = address
        self._uavs_by_source_address[medium, address] = uav

    def _configure_packet_handlers(self):
        """Constructs a mapping that maps FlockCtrl packet types to the
        handler functions that should be responsible for handling them.
        """
        return {
            StatusPacket: self._handle_inbound_status_packet,
            PrearmStatusPacket: self._handle_inbound_prearm_status_packet,
            CompressedCommandResponsePacket: self._handle_inbound_command_response_packet,
            CommandResponsePacket: self._handle_inbound_command_response_packet,
            AlgorithmDataPacket: self._handle_inbound_algorithm_data_packet,
            MissionInfoPacket: self._handle_inbound_mission_info_packet,
            MultiTargetCommandPacket: nop,
            RawGPSInjectionPacket: nop,
        }

    def _create_uav(self, formatted_id):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            formatted_id (str): the formatted string identifier of the UAV
                to create

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        return FlockCtrlUAV(formatted_id, driver=self)

    def _get_address_of_uav(self, uav: "FlockCtrlUAV") -> UAVAddress:
        """Returns the network address of the given UAV.

        Raises:
            ValueError: if no address is known for the UAV yet
        """
        try:
            return uav.preferred_address
        except ValueError:
            raise ValueError("Address of UAV is not known yet")

    def _get_or_create_uav(self, id):
        """Retrieves the UAV with the given numeric ID, or creates one if
        the driver has not seen a UAV with the given ID yet.

        Parameters:
            id (int): the numeric identifier of the UAV to retrieve

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        formatted_id = self._index_to_uav_id.get(id)
        if formatted_id is None:
            formatted_id = make_valid_object_id(self.id_format.format(id))
            self._index_to_uav_id[id] = formatted_id

        return self.app.object_registry.add_if_missing(
            formatted_id, factory=self._create_uav
        )

    def _handle_inbound_algorithm_data_packet(
        self, packet: AlgorithmDataPacket, source
    ):
        """Handles an inbound FlockCtrl packet containing algorithm-specific
        data.

        Parameters:
            packet: the packet to handle
            source: the source the packet was received from
        """
        uav = self._get_or_create_uav(packet.uav_id)
        try:
            algorithm = packet.algorithm
        except KeyError:
            algorithm = None

        if algorithm is not None:
            handle_algorithm_data_packet(
                algorithm,
                uav=uav,
                data=algorithm.decode_data_packet(packet),
                mutate=self.create_device_tree_mutator,
            )

    def _handle_inbound_command_response_packet(
        self, packet: CommandResponsePacketBase, source
    ):
        """Handles an inbound FlockCtrl command response packet.

        Parameters:
            packet: the packet to handle
            source: the source the packet was received from
        """
        body = self._packet_assembler.add_packet(packet, source)
        if body:
            if isinstance(packet, CompressedCommandResponsePacket):
                body = decompress(body)
            self._on_chunked_packet_assembled(body, source)

    def _handle_inbound_mission_info_packet(self, packet: MissionInfoPacket, source):
        """Handles an inbound FlockCtrl mision information packet.

        Parameters:
            packet: the packet to handle
            source: the source the packet was received from
        """
        try:
            uav = self._uavs_by_source_address[source]
        except KeyError:
            # Stale packet, ignore
            return

        uav.mission_name = packet.name

    def _handle_inbound_status_packet(self, packet: StatusPacket, source):
        """Handles an inbound FlockCtrl status packet.

        Parameters:
            packet: the packet to handle
            source: the source the packet was received from
        """
        uav = self._get_or_create_uav(packet.id)
        medium, address = source

        self._check_or_record_uav_address(uav, medium, address)

        # parse voltage level in proper format
        battery = BatteryInfo()
        battery.voltage = packet.voltage

        # parse light status in proper format
        if packet.light_status is None:
            light = None
        else:
            color = Color(
                red=packet.light_status.red / 255,
                green=packet.light_status.green / 255,
                blue=packet.light_status.blue / 255,
            )
            light = color_to_rgb565(color)

        # derive flight mode
        if packet.flags & StatusFlag.MODE_GUIDED:
            mode = (
                f"g{packet.algorithm_name[:3]}" if packet.algorithm_id > 0 else None
            ) or "guided"
        elif packet.flags & StatusFlag.MODE_STABILIZE:
            mode = "stabilize"
        elif packet.flags & StatusFlag.MODE_ALT_HOLD:
            mode = "alt hold"
        elif packet.flags & StatusFlag.MODE_LOITER:
            mode = "loiter"
        elif packet.flags & StatusFlag.MODE_AUTO:
            mode = "mission"
        elif packet.flags & StatusFlag.MODE_AUTOPILOT_LAND:
            mode = "land"
        elif packet.flags & StatusFlag.MODE_AUTOPILOT_RTH:
            mode = "rth"
        elif packet.flags & StatusFlag.MODE_OTHER:
            mode = "other"
        else:
            mode = "unknown"

        # derive GPS fix. Note that the status packets do not
        # contain information about the GPS fix if it is not
        # DGPS-augmented or RTK-based; in this case we pretend
        # that we have no GPS
        if packet.flags & StatusFlag.DGPS:
            gps_fix = GPSFixType.DGPS
        elif packet.flags & StatusFlag.RTK:
            gps_fix = GPSFixType.RTK_FLOAT
        else:
            gps_fix = GPSFixType.NO_GPS

        # derive debug information that includes the clock status and the
        # choreography index as well. This is because the Flockwave protocol
        # does not have a separate item for the choreography index and the
        # clock status.
        seconds = round(packet.clock_status.seconds) if packet.clock_status else 0
        minutes, seconds = divmod(seconds, 60)
        sep = ":" if seconds % 2 == 0 else "."
        debug = f"[{packet.choreography_index:02}] {minutes:02}{sep}{seconds:02} ["
        debug = [debug.encode("ascii"), packet.debug, b"]"]
        if uav.mission_name:
            debug.append(b" ")
            debug.append(uav.mission_name.encode("ascii", "ignore"))
        debug = b"".join(debug)

        # update whether we are probably airborne; we need this info to decide
        # whether we allow mission uploads or not
        uav._is_airborne = (
            packet.flags & (StatusFlag.MOTOR_RUNNING | StatusFlag.ON_GROUND)
            == StatusFlag.MOTOR_RUNNING
        )

        # Note: packet.flags & StatusFlag.PREARM is not handled here as
        # prearm status packets should arrive separately with more detail

        # update generic uav status
        uav.update_status(
            position=packet.location,
            gps=gps_fix,
            velocity=packet.velocity,
            heading=packet.heading,
            mode=mode,
            battery=battery,
            light=light,
            errors=map_flockctrl_error_code_and_flags(packet.error, packet.flags),
            debug=debug,
        )

        self.app.request_to_send_UAV_INF_message_for([uav.id])

    def _handle_inbound_prearm_status_packet(self, packet: PrearmStatusPacket, source):
        """Handles an inbound FlockCtrl prearm status packet.

        Parameters:
            packet: the packet to handle
            source: the source the packet was received from
        """
        _preflight_result_map = [
            PreflightCheckResult.OFF,
            PreflightCheckResult.PASS,
            PreflightCheckResult.FAILURE,
            PreflightCheckResult.RUNNING,
        ]

        try:
            uav = self._uavs_by_source_address[source]
        except KeyError:
            # Stale packet, ignore
            return

        for i, status in enumerate(packet.statuses):
            uav._preflight_status.set_result(
                id=PrearmCheck(i).name,
                result=_preflight_result_map[status],
                label=packet.index_to_description(i),
            )

        uav._preflight_status.update_summary()

    async def _handle_mission_upload(self, uav: "FlockCtrlUAV", data: bytes) -> None:
        """Uploads the given mission data file to a drone.

        Uploading a mission file when the drone is airborne is not permitted;
        doing so would yield a RuntimeError.

        Parameters:
            uav: the drone to upload the mission data to
            data: the contents of the mission file to upload

        Raises:
            RuntimeError: if the file cannot be uploaded
        """
        # prevent mission uploads if the drone is airborne
        if uav.is_airborne:
            raise RuntimeError("Cannot upload a mission while the drone is airborne")

        await to_thread.run_sync(
            upload_mission,
            data,
            uav.ssh_host,
            cancellable=True,
            limiter=self._upload_capacity,
        )

    def _on_chunked_packet_assembled(self, body, source):
        """Handler called when the response chunk handler has assembled
        the body of a chunked packet.

        Parameters:
            body (bytes): the assembled body of the packet
            source (Tuple[str, object]): source medium and address where the
                packet was sent from
        """
        try:
            uav = self._uavs_by_source_address[source]
        except KeyError:
            self.log.warn(
                "Reassembled chunked packet received from address "
                "{1!r} via {0!r} with no corresponding UAV".format(*source)
            )
            return

        decoded_body = body.decode("utf-8", errors="replace")
        try:
            self._pending_commands_by_uav[uav.id].set_result(decoded_body)
        except KeyError:
            self.log.warn(f"Dropped stale command response from UAV {uav.id}")

    def _run_in_background(self, func, *args) -> None:
        """Schedules the given asynchronous function to be executed in the
        background within the context of the extension.
        """
        # self.run_in_background is injected to the driver by the extension
        if self.run_in_background is None:
            raise RuntimeError("Extension is not running yet")
        else:
            return self.run_in_background(func, *args)

    async def _send_command_to_address(
        self, command: str, address
    ) -> CommandRequestPacket:
        """Sends a command packet with the given command string to the
        given UAV address.

        Parameters:
            command: the command to send. It will be encoded in UTF-8
                before sending it.
            address (object): the address to send the command to

        Returns:
            the packet that was sent
        """
        packet = CommandRequestPacket(command.encode("utf-8"))
        await self.send_packet(packet, address)
        return packet

    async def _send_command_to_uav(self, command, uav):
        """Sends a command string to the given UAV.

        Parameters:
            command (str): the command to send. It will be encoded in UTF-8
                before sending it.
            uav (FlockCtrlUAV): the UAV to send the command to

        Returns:
            the result of the command
        """
        address = self._get_address_of_uav(uav)
        await self._send_command_to_address(command, address)

        async with self._pending_commands_by_uav.new(
            uav.id, strict=not self.allow_multiple_commands_per_uav
        ) as future:
            try:
                return await future.wait()
            except FutureCancelled:
                return "Execution cancelled"

    async def _send_command_to_uav_and_check_for_errors(self, cmd, uav) -> None:
        """Sends a single command to a UAV and checks the response to determine
        whether it looks like an error.

        Returns:
            the result of the command

        Raises:
            RuntimeError: if the response looks like an error
        """
        response = await self._send_command_to_uav(cmd, uav)
        if response and response.startswith("/!\\"):
            raise RuntimeError(response[3:].strip())
        return response

    def _request_preflight_report_single(self, uav) -> PreflightCheckInfo:
        return uav.preflight_status

    async def _request_version_info_single(self, uav) -> VersionInfo:
        response = await self._send_command_to_uav_and_check_for_errors("version", uav)
        result = {}
        for line in response.splitlines():
            component, sep, version = line.partition(":")
            if sep:
                result[component] = version.strip()
        return result

    async def _send_fly_to_target_signal_single(self, uav, target):
        altitude = target.agl
        if altitude is not None:
            cmd = "go N{0.lat:.7f} E{0.lon:.7f} {1}".format(target, altitude)
        else:
            cmd = "go N{0.lat:.7f} E{0.lon:.7f}".format(target)
        return await self._send_command_to_uav_and_check_for_errors(cmd, uav)

    async def _send_reset_signal_single(self, uav, component, *, transport=None):
        if not component:
            # Resetting the whole UAV, this is supported
            return await self._send_command_to_uav_and_check_for_errors("restart", uav)
        else:
            # No component resets are implemented on this UAV yet
            raise RuntimeError(f"Resetting {component!r} is not supported")

    async def _send_shutdown_signal_single(self, uav, *, transport=None):
        return await self._send_command_to_uav_and_check_for_errors("halt", uav)

    def _uavs_to_ids(self, uavs):
        inverse_id_map = self._index_to_uav_id.inverse
        return [inverse_id_map.get(uav.id) for uav in uavs]


class FlockCtrlUAV(UAVBase):
    """Subclass for UAVs created by the driver for FlockCtrl-based
    drones.

    Attributes:
        addresses (Dict[str,object]): the addresses of this UAV, keyed by the
            various communication media that we may use to access the UAVs
            (e.g., ``wireless``)
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.addresses = {}
        self.mission_name = None
        self._is_airborne = False
        self._preflight_status = self._create_empty_preflight_status_report()

    async def calibrate_compass(self) -> str:
        """Calibrates the compass of the UAV.

        Returns:
            the result of the calibration command

        Raises:
            NotSupportedError: if the compass calibration is not supported on
                the UAV
        """
        # TODO: implement getting continuous status of compass calibration
        return await self.driver._send_command_to_uav_and_check_for_errors(
            f"calib compass", uav
        )

    async def calibrate_component(self, component: str) -> str:
        """Calibrates a component of the UAV.

        Parameters:
            component: the component to calibrate; currently we support
                ``baro``, ``compass``, ``gyro`` and ``level``.

        Returns:
            the result of the calibration command

        Raises:
            NotSupportedError: if the calibration of the given component is not
                supported on this UAV
            RuntimeError: if the UAV rejected to calibrate the component
        """
        if component == "compass":
            # Compass calibration is a whole different thing so that's handled
            # in a separate function
            return await self.calibrate_compass()
        elif component in ["baro", "gyro", "level"]:
            # rest of the components are simply handled as a proper generic calib command
            return await self.driver._send_command_to_uav_and_check_for_errors(
                f"calib {component}", uav
            )
        else:
            raise NotSupportedError

    @property
    def is_airborne(self) -> bool:
        """Returns whether the UAV is probably airborne (motors running and
        is not on ground).
        """
        return self._is_airborne

    @property
    def preferred_address(self) -> UAVAddress:
        """Returns the preferred medium and address via which the packets
        should be sent to this UAV.

        Returns:
            (str, object): the preferred medium and address of the UAV

        Throws:
            ValueError: if the UAV has no known address yet
        """
        for medium in ("wireless",):
            address = self.addresses.get(medium)
            if address is not None:
                return medium, address

        raise ValueError(
            "UAV has no address yet in any of the supported communication media"
        )

    @property
    def preflight_status(self) -> PreflightCheckInfo:
        return self._preflight_status

    @property
    def ssh_host(self) -> str:
        """Returns the hostname where the UAV is accessible for file uploads
        and commands via SSH.

        Returns:
            the SSH hostname of the UAV

        Throws:
            ValueError: if the UAV has no known SSH address yet
        """
        for medium in ("wireless",):
            address = self.addresses.get(medium)
            if address is not None:
                host, _ = address
                return host

        raise ValueError("UAV has no SSH hostname yet")

    def update_detected_features(self, itow, features, mutator):
        """Updates the visual features detected by the camera attached to the
        UAV with the given new value.

        Parameters:
            itow (int): timestamp corresponding to the measurement
            features (List[GPSCoordinate]): the positions of the features
                detected
            mutator (DeviceTreeMutator): the mutator object that can be used
                to manipulate the device tree nodes
        """
        position = self._status.position
        if position is None:
            return

        for i, position in enumerate(features):
            pos_data = {"lat": position.lat, "lon": position.lon}
            if position.amsl is not None:
                pos_data["amsl"] = position.amsl
            if position.agl is not None:
                pos_data["agl"] = position.agl

            # TODO: what value do we assign to the measurement?
            mutator.update(self.camera_features[i], dict(pos_data, value=itow))

    def update_geiger_counter(self, position, itow, dose_rate, raw_counts, mutator):
        """Updates the value of the Geiger counter of the UAV with the given
        new value.

        Parameters:
            position (GPSCoordinate): the position where the measurement was
                taken
            itow (int): timestamp corresponding to the measurement
            dose_rate (Optional[float]): the new measured dose rate or ``None``
                if the Geiger counter was disabled
            raw_counts (List[int]): the raw counts from the Geiger counter
                tubes or ``None`` if the Geiger counter was disabled
            mutator (DeviceTreeMutator): the mutator object that can be used
                to manipulate the device tree nodes
        """
        position = self._status.position
        if position is None:
            return

        pos_data = {"lat": position.lat, "lon": position.lon}
        if position.amsl is not None:
            pos_data["amsl"] = position.amsl
        if position.agl is not None:
            pos_data["agl"] = position.agl

        mutator.update(self.geiger_counter_dose_rate, dict(pos_data, value=dose_rate))

        devices = self.geiger_counter_raw_counts
        values = raw_counts
        for device, value in zip(devices, values):
            mutator.update(device, dict(pos_data, value=value))

        if self._last_geiger_counter_packet is not None:
            last_itow, last_raw_counts = self._last_geiger_counter_packet
            dt = (itow - last_itow) / 1000
            if dt > 0:
                devices = self.geiger_counter_rates
                values = [
                    (value - last_value) / dt if value > last_value else None
                    for value, last_value in zip(raw_counts, last_raw_counts)
                ]
                for device, value in zip(devices, values):
                    if value is not None:
                        mutator.update(device, dict(pos_data, value=value))

        self._last_geiger_counter_packet = (itow, raw_counts)

    @staticmethod
    def _create_empty_preflight_status_report() -> PreflightCheckInfo:
        """Creates an empty preflight status report that will be updated
        periodically.
        """
        report = PreflightCheckInfo()
        for check in PrearmCheck:
            report.add_item(check.name, check.description)
        report.update_summary()
        return report

    def _initialize_device_tree_node(self, node):
        # add geiger muller counter node and measurement channels
        device = node.add_device("geiger_counter")
        self.geiger_counter_dose_rate = device.add_channel(
            "dose_rate", type=object, unit="mGy/h"
        )
        self.geiger_counter_raw_counts = [
            device.add_channel("raw_count_{0}".format(i), type=object)
            for i in range(MAX_GEIGER_TUBE_COUNT)
        ]
        self.geiger_counter_rates = [
            device.add_channel("rate_{0}".format(i), type=object, unit="count/sec")
            for i in range(MAX_GEIGER_TUBE_COUNT)
        ]
        self._last_geiger_counter_packet = None
        # add camera node and feature channels
        # TODO: should we have a single channel and store all new features there
        # or should we have multiple features or maybe some feature IDs sent
        # from flockctrl to be able to assign features here to there?
        device = node.add_device("camera")
        self.camera_features = [
            device.add_channel("feature_{0}".format(i), type=object)
            for i in range(MAX_CAMERA_FEATURE_COUNT)
        ]
