"""Driver class for FlockCtrl-based drones."""

from __future__ import division

from colour import Color
from flockwave.concurrency import FutureCancelled, FutureMap
from flockwave.protocols.flockctrl.packets import (
    ChunkedPacketAssembler,
    AlgorithmDataPacket,
    CommandRequestPacket,
    CommandResponsePacket,
    CompressedCommandResponsePacket,
    MissionInfoPacket,
    PrearmStatusPacket,
    StatusPacket,
)
from flockwave.server.ext.logger import log
from flockwave.server.model.uav import BatteryInfo, UAVBase, UAVDriver
from flockwave.server.utils import color_to_rgb565, nop
from flockwave.spec.ids import make_valid_object_id
from time import time
from typing import Optional
from zlib import decompress

from .algorithms import handle_algorithm_data_packet
from .errors import AddressConflictError, map_flockctrl_error_code

__all__ = ("FlockCtrlDriver",)

MAX_GEIGER_TUBE_COUNT = 2
MAX_CAMERA_FEATURE_COUNT = 32


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
    """

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (SkybrushServer): the app in which the driver lives
            id_format (str): the format of the UAV IDs used by this driver.
                See the class documentation for more details.
        """
        super(FlockCtrlDriver, self).__init__()

        self._pending_commands_by_uav = FutureMap()

        self._disable_warnings_until = {}
        self._packet_handlers = self._configure_packet_handlers()
        self._packet_assembler = ChunkedPacketAssembler()
        self._index_to_uav_id = {}
        self._uavs_by_source_address = {}

        self.allow_multiple_commands_per_uav = True
        self.app = app
        self.create_device_tree_mutator = None
        self.id_format = id_format
        self.log = log.getChild("flockctrl").getChild("driver")
        self.send_packet = None

    def _are_addresses_in_conflict(self, old, new) -> bool:
        """Checks whether two UAV addresses on the same communication medium
        are in conflict or not.

        It is guaranteed that the two addresses are not equal when this function
        is invoked.

        The current implementation works as follows. If the old address and
        the new address are both associated to localhost, the two addresses
        are assumed to be compatible. (In testing scenarios, it happens a lot
        that the ports from which the virtual UAVs broadcast their messages
        change over time if one of the UAVs is restarted). Otherwise, the
        two addresses are deemed incompatible.
        """
        old_ip, _ = old
        new_ip, _ = new
        if old_ip == new_ip and old_ip in ("127.0.0.1", "::1"):
            return False
        else:
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

        uav.addresses[medium] = address
        self._uavs_by_source_address[medium, address] = uav

    def _configure_packet_handlers(self):
        """Constructs a mapping that maps FlockCtrl packet types to the
        handler functions that should be responsible for handling them.
        """
        return {
            StatusPacket: self._handle_inbound_status_packet,
            PrearmStatusPacket: nop,
            CompressedCommandResponsePacket: self._handle_inbound_command_response_packet,
            CommandResponsePacket: self._handle_inbound_command_response_packet,
            AlgorithmDataPacket: self._handle_inbound_algorithm_data_packet,
            MissionInfoPacket: nop,
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

        object_registry = self.app.object_registry
        if not object_registry.contains(formatted_id):
            uav = self._create_uav(formatted_id)
            object_registry.add(uav)
        return object_registry.find_by_id(formatted_id)

    def handle_generic_command(self, uav, command, args, kwds):
        """Sends a generic command execution request to the given UAV."""
        command = " ".join([command, *args])
        return self._send_command_to_uav(command, uav)

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
            now = time()
            if now >= deadline:
                self.log.warn(
                    "Dropped packet from invalid source: "
                    "{0}/{1}, sent to UAV {2}".format(ex.medium, ex.address, uav_id)
                )
                self._disable_warnings_until[uav_id] = now + 1

    def validate_command(self, command: str, args, kwds) -> Optional[str]:
        # Prevent the usage of keyword arguments; they are not supported.
        # Also prevent non-string positional arguments.
        if kwds:
            return "Keyword arguments not supported"
        if args and any(not isinstance(arg, str) for arg in args):
            return "Non-string positional arguments not supported"

    def _handle_inbound_algorithm_data_packet(self, packet, source):
        """Handles an inbound FlockCtrl packet containing algorithm-specific
        data.

        Parameters:
            packet (AlgorithmDataPacket): the packet to handle
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

    def _handle_inbound_command_response_packet(self, packet, source):
        """Handles an inbound FlockCtrl command response packet.

        Parameters:
            packet (CommandResponsePacketBase): the packet to handle
            source: the source the packet was received from
        """
        body = self._packet_assembler.add_packet(packet, source)
        if body:
            if isinstance(packet, CompressedCommandResponsePacket):
                body = decompress(body)
            self._on_chunked_packet_assembled(body, source)

    def _handle_inbound_status_packet(self, packet, source):
        """Handles an inbound FlockCtrl status packet.

        Parameters:
            packet (StatusPacket): the packet to handle
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
                blue=packet.light_status.blue / 255
            )
            light = color_to_rgb565(color)

        # update generic uav status
        uav.update_status(
            position=packet.location,
            velocity=packet.velocity,
            heading=packet.heading,
            algorithm=packet.algorithm_name,
            battery=battery,
            light=light,
            errors=map_flockctrl_error_code(packet.error),
        )

        self.app.request_to_send_UAV_INF_message_for([uav.id])

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

    async def _send_command_to_uav(self, command, uav):
        """Sends a command string to the given UAV.

        Parameters:
            command (str): the command to send. It will be encoded in UTF-8
                before sending it.
            uav (FlockCtrlUAV): the UAV to send the command to

        Returns:
            the result of the command
        """
        try:
            address = uav.preferred_address
        except ValueError:
            raise ValueError("Address of UAV is not known yet")

        await self._send_command_to_address(command, address)

        async with self._pending_commands_by_uav.new(
            uav.id, strict=not self.allow_multiple_commands_per_uav
        ) as future:
            try:
                return await future.wait()
            except FutureCancelled:
                return "Execution cancelled"

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

    async def _send_fly_to_target_signal_single(self, uav, target):
        altitude = target.agl
        if altitude is not None:
            cmd = "go N{0.lat:.7f} E{0.lon:.7f} {1}".format(target, altitude)
        else:
            cmd = "go N{0.lat:.7f} E{0.lon:.7f}".format(target, altitude)
        return await self._send_command_to_uav(cmd, uav)

    async def _send_landing_signal_single(self, uav):
        return await self._send_command_to_uav("land", uav)

    async def _send_return_to_home_signal_single(self, uav):
        return await self._send_command_to_uav("rth", uav)

    async def _send_shutdown_signal_single(self, uav):
        return await self._send_command_to_uav("halt", uav)

    async def _send_takeoff_signal_single(self, uav):
        return await self._send_command_to_uav("motoron", uav)


class FlockCtrlUAV(UAVBase):
    """Subclass for UAVs created by the driver for FlockCtrl-based
    drones.

    Attributes:
        addresses (Dict[str,object]): the addresses of this UAV, keyed by the
            various communication media that we may use to access the UAVs
            (e.g., ``wireless``)
    """

    def __init__(self, *args, **kwds):
        super(FlockCtrlUAV, self).__init__(*args, **kwds)
        self.addresses = {}

    @property
    def preferred_address(self):
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
