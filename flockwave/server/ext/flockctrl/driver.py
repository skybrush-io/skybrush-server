"""Driver class for FlockCtrl-based drones."""

from bidict import bidict
from flockwave.server.ext.logger import log
from flockwave.server.model.uav import UAVBase, UAVDriver

from .errors import AddressConflictError
from .packets import FlockCtrlCommandRequestPacket, \
    FlockCtrlCommandResponsePacket, FlockCtrlStatusPacket, \
    ChunkedPacketAssembler

__all__ = ("FlockCtrlDriver", )


class FlockCtrlDriver(UAVDriver):
    """Driver class for FlockCtrl-based drones.

    Attributes:
        app (FlockwaveServer): the app in which the driver lives
        id_format (str): Python format string that receives a numeric
            drone ID in the flock and returns its preferred formatted
            identifier that is used when the drone is registered in the
            server, or any other object that has a ``format()`` method
            accepting a single integer as an argument and returning the
            preferred UAV identifier.
        send_packet (callable): a function that should be called by the
            driver whenever it wants to send a packet. The function must
            be called with the packet to send and the destination address
            (``None`` if the packet should be broadcast)
    """

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (FlockwaveServer): the app in which the driver lives
            id_format (str): the format of the UAV IDs used by this driver.
                See the class documentation for more details.
        """
        self._app = None
        super(FlockCtrlDriver, self).__init__()
        self._commands_by_uav = bidict()
        self._packet_handlers = self._configure_packet_handlers()
        self._packet_assembler = ChunkedPacketAssembler()
        self._packet_assembler.packet_assembled.connect(
            self._on_chunked_packet_assembled, sender=self._packet_assembler
        )
        self._uavs_by_source_address = {}
        self.app = app
        self.id_format = id_format
        self.log = log.getChild("flockctrl").getChild("driver")
        self.send_packet = None

    @property
    def app(self):
        """The app in which the driver lives."""
        return self._app

    @app.setter
    def app(self, value):
        if self._app == value:
            return

        if self._app:
            cmd_manager = self.app.command_execution_manager
            cmd_manager.expired.disconnect(self._on_command_expired)
            cmd_manager.finished.disconnect(self._on_command_finished)

        self._app = value

        if self._app:
            cmd_manager = self.app.command_execution_manager
            cmd_manager.expired.connect(self._on_command_expired,
                                        sender=cmd_manager)
            cmd_manager.finished.connect(self._on_command_finished,
                                         sender=cmd_manager)

    def _check_or_record_uav_address(self, uav, source_address):
        """Records that the given UAV has the given XBee address,
        or, if the UAV already has an address, checks whether the
        address matches the one provided to this function.

        Parameters:
            uav (FlockCtrlUAV): the UAV to check
            source_address (bytes): the XBee source address of the UAV

        Raises:
            AddressConflictError: if the UAV already has an address and it
                is different from the one given to this function
        """
        uav.check_or_record_address(source_address)
        self._uavs_by_source_address[source_address] = uav

    def _configure_packet_handlers(self):
        """Constructs a mapping that maps FlockCtrl packet types to the
        handler functions that should be responsible for handling them.
        """
        return {
            FlockCtrlStatusPacket:
                self._handle_inbound_status_packet,
            FlockCtrlCommandResponsePacket:
                self._handle_inbound_command_response_packet
        }

    def _create_uav(self, formatted_id):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            formatted_id (str): the formatted string identifier of the UAV
                to create

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        uav = FlockCtrlUAV(formatted_id, driver=self)
        return uav

    def _get_or_create_uav(self, id):
        """Retrieves the UAV with the given numeric ID, or creates one if
        the driver has not seen a UAV with the given ID yet.

        Parameters:
            id (int): the numeric identifier of the UAV to retrieve

        Returns:
            FlockCtrlUAV: an appropriate UAV object
        """
        formatted_id = self.id_format.format(id)
        uav_registry = self.app.uav_registry
        if not uav_registry.contains(formatted_id):
            uav = self._create_uav(formatted_id)
            uav_registry.add(uav)
        return uav_registry.find_by_id(formatted_id)

    def handle_generic_command(self, uavs, command, args, kwds):
        """Sends a generic command execution request to the given UAVs."""
        result = {}
        error = None

        # Prevent the usage of keyword arguments; they are not supported.
        # Also prevent non-string positional arguments.
        if kwds:
            error = "Keyword arguments not supported"
        elif args:
            if any(not isinstance(arg, str) for arg in args):
                error = "Non-string positional arguments not supported"
            else:
                command = [command]
                command.extend(args)
                command = " ".join(command)

        for uav in uavs:
            if error:
                result[uav] = error
            else:
                result[uav] = self._send_command_to_uav(command, uav)

        return result

    def handle_inbound_packet(self, packet):
        """Handles an inbound FlockCtrl packet received over an XBee
        connection.
        """
        packet_class = packet.__class__
        handler = self._packet_handlers.get(packet_class)
        if handler is None:
            self.log.warn("No packet handler defined for packet "
                          "class: {0}".format(packet_class.__name__))
        else:
            handler(packet)

    def _handle_inbound_command_response_packet(self, packet):
        """Handles an inbound FlockCtrl command response packet.

        Parameters:
            packet (FlockCtrlCommandResponsePacket): the packet to handle
        """
        self._packet_assembler.add_packet(packet)

    def _handle_inbound_status_packet(self, packet):
        """Handles an inbound FlockCtrl status packet.

        Parameters:
            packet (FlockCtrlStatusPacket): the packet to handle
        """
        uav = self._get_or_create_uav(packet.id)
        self._check_or_record_uav_address(uav, packet.source_address)
        uav.update_status(
            position=packet.location,
            velocity=packet.velocity,
            heading=packet.heading
        )
        # TODO: rate limiting
        message = self.app.create_UAV_INF_message_for([uav.id])
        self.app.message_hub.send_message(message)

    def _on_chunked_packet_assembled(self, sender, body, source_address):
        """Handler called when the response chunk handler has assembled
        the body of a chunked packet.

        Parameters:
            body (bytes): the assembled body of the packet
            source_address (bytes): source address of the XBee device that
                sent the packet
        """
        try:
            uav = self._uavs_by_source_address[source_address]
        except KeyError:
            self.log.warn("Reassembled chunked packet received from address "
                          "{0!r} with no corresponding UAV"
                          .format(source_address))
            return
        try:
            command = self._commands_by_uav[uav.id]
        except KeyError:
            self.log.warn("Dropped stale command response from UAV "
                          "{0.id}".format(uav))
            return

        decoded_body = body.decode("utf-8", errors="replace")
        cmd_manager = self.app.command_execution_manager
        cmd_manager.finish(command.id, decoded_body)

    def _on_command_expired(self, sender, statuses):
        """Handler called when a command being executed by the command
        manager has expired (i.e. timed out). Finds the command in the
        drone-to-command mapping and deletes it so we can send another
        command for the drone.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
                of the app that was responsible for handling the command
            statuses (List[CommandExecutionStatus]): the commands that have
                been expired by the manager
        """
        uavs_by_command = self._commands_by_uav.inv
        for status in statuses:
            uavs_by_command.pop(status, None)

    def _on_command_finished(self, sender, status):
        """Handler called when a command being executed by the command
        manager has fnished. Finds the command in the drone-to-command
        mapping and deletes it so we can send another command for the drone.
        Nothing else has to be done there -- the response packet to the
        Flockwave clients is dispatched by the command execution manager so
        we don't have to deal with that.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
                of the app that was responsible for handling the command
            status (CommandExecutionStatus): the command that has finished
                execution.
        """
        uavs_by_command = self._commands_by_uav.inv
        uavs_by_command.pop(status, None)

    def _send_command_to_uav(self, command, uav):
        """Sends a command string to the given UAV.

        Parameters:
            command (str): the command to send. It will be encoded in UTF-8
                before sending it.
            uav (FlockCtrlUAV): the UAV to send the command to

        Returns:
            CommandExecutionStatus: the execution status object for
                the command if it has been sent to the UAV, ``False`` or
                a string describing the reason of failure if it has not
                been sent
        """
        existing_command = self._commands_by_uav.get(uav.id)
        if existing_command is not None:
            return "Another command (receipt ID={0.id}) is already "\
                   "in progress".format(existing_command)

        if uav.address is None:
            return "XBee address of UAV is not known yet"

        cmd_manager = self.app.command_execution_manager
        self._commands_by_uav[uav.id] = receipt = cmd_manager.start()

        packet = FlockCtrlCommandRequestPacket(command.encode("utf-8"))
        self.send_packet(packet, uav.address)

        return receipt


class FlockCtrlUAV(UAVBase):
    """Subclass for UAVs created by the driver for FlockCtrl-based
    drones.

    Attributes:
        address (bytes): the XBee address of this UAV
    """

    def __init__(self, id, driver, address=None):
        super(FlockCtrlUAV, self).__init__(id, driver)
        self.address = address

    def check_or_record_address(self, address):
        """When this UAV has no known XBee address yet (i.e.
        ``self.address`` is ``None``), stores the given address as the
        XBee address of this UAV. When this UAV has an XBee address, checks
        whether the address is equal to the given one and raises an
        AddressConflictError if the two addresses are not equal

        Parameters:
            address (bytes): the XBee address of this UAV

        Raises:
            AddressConflictError: if the UAV already has an address and it
                is different from the one given to this function
        """
        if self.address is None:
            self.address = address
        elif self.address != address:
            raise AddressConflictError(self, address)
