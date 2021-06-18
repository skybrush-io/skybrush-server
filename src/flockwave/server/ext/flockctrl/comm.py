"""Communication manager that facilitates communication between a flockctrl-based
UAV and the ground station via some communication link.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from paramiko import SSHClient
from select import select
from trio import Event
from trio_util import periodic
from typing import Any, Iterable, List, Optional, TYPE_CHECKING, Tuple, Union

from flockwave.channels import MessageChannel
from flockwave.connections import (
    Connection,
    IPAddressAndPort,
    StreamConnectionBase,
    UDPSocketConnection,
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

if TYPE_CHECKING:
    from .driver import FlockCtrlDriver

__all__ = ("create_communication_manager", "execute_ssh_command", "upload_mission")


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
    if isinstance(connection, UDPSocketConnection):
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
    channel = MessageChannel(
        connection,
        parser=constant(()),
        encoder=FlockCtrlEncoder.create_radio_encoder_function(
            log=log, source_address=0
        ),
    )
    channel.broadcast_address = 32767

    return channel


def create_flockctrl_udp_message_channel(
    connection: UDPSocketConnection, log: Logger
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
    channel = MessageChannel(
        connection,
        parser=FlockCtrlParser.create_udp_parser_function(log),
        encoder=FlockCtrlEncoder.create_udp_encoder_function(log),
    )

    if hasattr(connection, "broadcast_address"):
        channel.broadcast_address = connection.broadcast_address

    return channel


def execute_ssh_command(
    ssh: SSHClient, command: str, stdin: Optional[bytes] = None, timeout: float = 5
):
    """Executes the given command on an established SSH connection, optionally
    submitting the given data on the standard input stream before reading
    anything from stdout or stderr. stdout and stderr is then read until the
    end.

    Parameters:
        ssh: the SSH connection
        command: the command string to send
        stdin: optional input to send on the standard input
        timeout: number of seconds to wait for a response before the attempt is
            considered to have timed out

    Returns:
        Tuple[bytes, bytes, int]: the data read from the standard output and
        standard error streams as well as the exit code of the command
    """
    stdin_stream, stdout_stream, _ = ssh.exec_command(command, timeout=timeout)
    channel = stdout_stream.channel

    if stdin is not None:
        stdin_stream.write(stdin)

    channel.shutdown_write()
    stdin_stream.close()

    stdout, stderr = [], []

    while True:
        rl, _, _ = select([channel], [], [])
        if rl:
            num_bytes = 0
            if channel.recv_stderr_ready():
                recv_bytes = channel.recv_stderr(1024)
                num_bytes += len(recv_bytes)
                stderr.append(recv_bytes)
            if channel.recv_ready():
                recv_bytes = channel.recv(1024)
                num_bytes += len(recv_bytes)
                stdout.append(recv_bytes)
            if not num_bytes:
                break

    channel.close()

    exit_code = channel.recv_exit_status()
    if exit_code == -1:
        # Bad, bad server...
        exit_code = 0

    return b"".join(stdout), b"".join(stderr), exit_code


def format_flockctrl_address(address: Any) -> str:
    """Returns a formatted representation of the address of a `flockctrl`
    message channel.
    """
    try:
        return format_socket_address(address)
    except ValueError:
        return str(address)


def upload_mission(raw_data: bytes, address: Union[str, Tuple[str, int]]) -> None:
    """Uploads the given raw mission data to the inbox of a drone at the given
    address.

    This function blocks the thread it is running in; it is advised to run it
    in a separate thread in order not to block the main event loop.

    Parameters:
        raw_data: the raw data to upload. It must be an in-memory mission ZIP
            file. Some basic validity checks will be performed on it before
            attempting the upload.
        address: the network address of the UAV, either as a hostname or as a
            tuple consisting of a hostname and a port
    """
    from zipfile import ZipFile
    from .ssh import open_scp, open_ssh

    with ZipFile(BytesIO(raw_data)) as parsed_data:
        if parsed_data.testzip():
            raise ValueError("Invalid mission file")

        version_info = parsed_data.read("_meta/version").strip()
        if version_info != b"1":
            raise ValueError("Only version 1 mission files are supported")

        if "mission.cfg" not in parsed_data.namelist():
            raise ValueError("No mission configuration in mission file")

    name = (
        datetime.now()
        .replace(microsecond=0)
        .isoformat()
        .replace(":", "")
        .replace("-", "")
    )

    # TODO(ntamas): this thread needs to be interruptible, otherwise we are
    # blocking one entry in the CapacityLimiter even if the upstream request
    # timed out. Using a progress handler in putfo() and then throwing an
    # exception from there would probably work as an interruption, but even the
    # progress handler is not called regularly if the connection is stuck so we
    # probably need another solution (e.g., use another process?)
    with open_ssh(address, username="root") as ssh:
        scp = open_scp(ssh)
        scp.putfo(BytesIO(raw_data), f"/tmp/{name}.mission-tmp")
        _, _, exit_code = execute_ssh_command(
            ssh,
            " && ".join(
                [
                    f"mv /tmp/{name}.mission-tmp /data/inbox/{name}.mission",
                    "systemctl restart flockctrl",
                ]
            ),
        )

        if exit_code != 0:
            raise ValueError("Failed to restart flockctrl process")


#: Number of commands supported by the multi-target messages in the
#: flockctrl protocol
NUM_COMMANDS = 16


class BurstedMultiTargetMessageManager:
    """Class that is responsible for sending multi-target messages to the
    drones in the flock and keeping track of sequence numbers.
    """

    def __init__(self, driver: "FlockCtrlDriver"):
        """Constructor."""
        self._driver = driver
        self._sequence_ids: List[int] = [0] * NUM_COMMANDS
        self._active_burst_cancellations: List[Optional[Event]] = [None] * NUM_COMMANDS

    def schedule_burst(
        self, command: MultiTargetCommand, uav_ids: Iterable[int], duration: float
    ) -> None:
        """Schedules a bursted simple command execution targeting multiple UAVs.

        Parameters:
            command: the command code to send
            uav_ids: the IDs of the UAVs to target. The IDs presented here are
                the numeric IDs in the FlockCtrl network, not the global UAV IDs.
            duration: duration of the burst, in seconds.
        """
        if self._active_burst_cancellations[command]:
            # Cancel the previous burst for this command
            self._active_burst_cancellations[command].set()

        event = self._active_burst_cancellations[command] = Event()
        self._driver.run_in_background(
            self._execute_burst, command, uav_ids, duration, event
        )

    async def _execute_burst(
        self,
        command: MultiTargetCommand,
        uav_ids: Iterable[int],
        duration: float,
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

        async for elapsed, _ in periodic(0.1):
            if elapsed >= duration or cancelled_event.is_set():
                break

            await self._driver.broadcast_packet(packet, "wireless")
