"""Communication manager that facilitates communication between a flockctrl-based
UAV and the ground station via some communication link.
"""

from __future__ import annotations

from trio import Event
from trio_util import periodic
from typing import Any, Iterable, List, Optional, TYPE_CHECKING, Tuple, Union

from flockwave.channels import MessageChannel
from flockwave.connections import (
    Connection,
    IPAddressAndPort,
    StreamConnectionBase,
    UDPListenerConnection,
)
from flockwave.logger import Logger
from flockwave.networking import format_socket_address
from flockwave.protocols.flockctrl import (
    FlockCtrlEncoder,
    FlockCtrlPacket,
    FlockCtrlParser,
    MultiTargetCommand,
)
from flockwave.protocols.flockctrl.packets import MultiTargetCommandPacket
from flockwave.server.comm import CommunicationManager
from flockwave.server.utils import constant

from flockwave.server.model.transport import TransportOptions

if TYPE_CHECKING:
    from .driver import FlockCtrlDriver

__all__ = ("create_communication_manager",)


def create_communication_manager() -> CommunicationManager[
    FlockCtrlPacket, IPAddressAndPort
]:
    """Creates a communication manager instance for the extension."""
    return CommunicationManager(
        channel_factory=create_flockctrl_message_channel,
        format_address=format_flockctrl_address,
    )


def create_flockctrl_message_channel(
    connection: Connection, log: Logger
) -> MessageChannel[Tuple[FlockCtrlPacket, Union[int, IPAddressAndPort]]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given connection, and handles the parsing of `flockctrl`
    messages automaticaly. The channel will accept and yield tuples containing
    a FlockCtrlPacket_ object and a corresponding address; the address is
    connection-dependent. For UDP connections, the address is a tuple consisting
    of an IP address and a port. For radio connections, the address is an
    integer where zero denotes the ground station and 32767 is the broadcast
    address.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged

    Returns:
        the message channel

    Raises:
        TypeError: if we do not support the given connection type in this
            extension
    """
    if isinstance(connection, UDPListenerConnection):
        return create_flockctrl_udp_message_channel(connection, log)  # type: ignore
    elif isinstance(connection, StreamConnectionBase):
        return create_flockctrl_radio_message_channel(connection, log)  # type: ignore

    raise TypeError(f"Connection type not supported: {connection.__class__.__name__}")


def create_flockctrl_radio_message_channel(
    connection: StreamConnectionBase, log: Logger
) -> MessageChannel[Tuple[FlockCtrlPacket, int]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given radio connection, and handles the parsing of
    `flockctrl` messages automatically. The channel will accept and yield
    tuples containing a FlockCtrlPacket_ object and an address, which is an
    integer that uniquely identifies drones and the ground station.

    By convention, the address of the ground station is 0 and the broadcast
    address is 32767.

    Parameters:
        connection: the connection to write data to
        log: the logger on which any error messages and warnings should be logged

    Returns:
        the message channel
    """

    # TODO(ntamas): the parser does nothing for the time being, just consumes
    # everything
    channel: MessageChannel[Tuple[FlockCtrlPacket, int]] = MessageChannel(
        connection,
        parser=constant(()),
        encoder=FlockCtrlEncoder.create_radio_encoder_function(
            log=log, source_address=0
        ),
    )
    channel.broadcast_address = 32767

    return channel


def create_flockctrl_udp_message_channel(
    connection: UDPListenerConnection, log: Logger
) -> MessageChannel[Tuple[FlockCtrlPacket, IPAddressAndPort]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given UDP connection, and handles the parsing of
    `flockctrl` messages automatically. The channel will accept and yield
    tuples containing a FlockCtrlPacket_ object and an IP address-port pair.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged

    Returns:
        the message channel
    """
    channel: MessageChannel[Tuple[FlockCtrlPacket, IPAddressAndPort]] = MessageChannel(
        connection,
        parser=FlockCtrlParser.create_udp_parser_function(log),
        encoder=FlockCtrlEncoder.create_udp_encoder_function(log),
    )

    if hasattr(connection, "broadcast_address"):
        channel.broadcast_address = connection.broadcast_address

    return channel


def format_flockctrl_address(address: Any) -> str:
    """Returns a formatted representation of the address of a `flockctrl`
    message channel.
    """
    try:
        return format_socket_address(address)
    except ValueError:
        return str(address)


#: Number of commands supported by the multi-target messages in the
#: flockctrl protocol
NUM_COMMANDS = 16


class BurstedMultiTargetMessageManager:
    """Class that is responsible for sending multi-target messages to the
    drones in the flock and keeping track of sequence numbers.
    """

    _driver: "FlockCtrlDriver"
    _sequence_ids: List[int]
    _active_burst_cancellations: List[Optional[Event]]

    def __init__(self, driver: "FlockCtrlDriver"):
        """Constructor."""
        self._driver = driver
        self._sequence_ids = [0] * NUM_COMMANDS
        self._active_burst_cancellations = [None] * NUM_COMMANDS

    def schedule_burst(
        self,
        command: MultiTargetCommand,
        uav_ids: Iterable[int],
        duration: float,
        transport: Optional[TransportOptions] = None,
    ) -> None:
        """Schedules a bursted simple command execution targeting multiple UAVs.

        Parameters:
            command: the command code to send
            uav_ids: the IDs of the UAVs to target. The IDs presented here are
                the numeric IDs in the FlockCtrl network, not the global UAV IDs.
            duration: duration of the burst, in seconds
            transport: transport options for sending the command
        """
        cancel_event = self._active_burst_cancellations[command]
        if cancel_event:
            # Cancel the previous burst for this command
            cancel_event.set()

        event = self._active_burst_cancellations[command] = Event()
        self._driver.run_in_background(
            self._execute_burst, command, uav_ids, duration, transport, event
        )

    async def _execute_burst(
        self,
        command: MultiTargetCommand,
        uav_ids: Iterable[int],
        duration: float,
        transport: Optional[TransportOptions],
        cancelled_event: Event,
    ) -> None:
        """Performs a bursted simple command transmission targeting multiple
        UAVs.

        The command packet will be repeated once every 100 msec, until the given
        duration.

        Parameters:
            command: the command code to send
            uav_ids: the IDs of the UAVs to target. The IDs presented here are
                the numeric IDs in the FlockCtrl network, not the global UAV IDs.
            duration: duration of the burst, in seconds.
            cancelled_event: a Trio event that can be used to cancel the burst
        """
        packet = MultiTargetCommandPacket(
            list(uav_ids), command=command, sequence_id=self._sequence_ids[command]
        )
        self._sequence_ids[command] += 1

        channels: List[str] = ["wireless"]

        # When the user tries to send over the secondary channel, we send over
        # _both_ wifi and radio. This is not entirely consistent with how the
        # TransportOptions object is specified in the specs.
        if TransportOptions.is_secondary(transport):
            channels.append("radio")

        async for elapsed, _ in periodic(0.1):
            if elapsed >= duration or cancelled_event.is_set():
                break

            for channel in channels:
                await self._driver.broadcast_packet(packet, channel)
