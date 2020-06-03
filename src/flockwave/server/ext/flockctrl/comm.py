"""Base classes for implementing communication managers that facilitate
communication between UAVs and a ground station via some communication
link (e.g., standard 802.11 wifi).
"""

import attr

from collections import defaultdict
from datetime import datetime
from functools import partial
from io import BytesIO
from paramiko import SSHClient
from select import select
from trio import open_memory_channel
from trio_util import wait_all
from typing import Generator, Optional, Tuple, Union

from flockwave.channels import MessageChannel
from flockwave.connections import Connection, IPAddressAndPort, UDPSocketConnection
from flockwave.logger import Logger
from flockwave.networking import format_socket_address
from flockwave.protocols.flockctrl import (
    FlockCtrlEncoder,
    FlockCtrlPacket,
    FlockCtrlParser,
)

__all__ = (
    "create_flockctrl_udp_message_channel",
    "CommunicationManager",
    "execute_ssh_command",
    "upload_mission",
)


def create_flockctrl_udp_message_channel(
    connection: UDPSocketConnection, log: Logger
) -> MessageChannel[Tuple[FlockCtrlPacket, IPAddressAndPort]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given UDP connection, and does the parsing of
    `flockctrl` messages automatically. The channel will accept and yield
    tuples containing an IP address - port pair and a FlockCtrlPacket_ object.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    return MessageChannel(
        connection,
        parser=FlockCtrlParser.create_udp_parser_function(log),
        encoder=FlockCtrlEncoder.create_udp_encoder_function(log),
    )


class CommunicationManager:
    """Communication manager class with multiple responsibilities:

    - watches a set of connections and uses the app supervisor to keep them
      open

    - parses the incoming messages from each of the connections in separate
      tasks, and forwards them to a central queue

    - provides a method that can be used to send a message on any of the
      currently open connections
    """

    @attr.s
    class Entry:
        """A single entry in the communication manager that contains a connection
        managed by the manager and its associated data.
        """

        connection: Connection = attr.ib()
        name: str = attr.ib()

    def __init__(self):
        self._entries_by_name = defaultdict(list)
        self._running = False

    def add(self, connection, *, name):
        """Adds the given connection to the list of connections managed by
        the communication manager.

        Parameters:
            connection: the connection to add
            name: the name of the connection; passed back to consumers of the
                incoming packet queue along with the received packets so they
                know which connection the packet was received from
        """
        if self._running:
            raise RuntimeError("cannot add new connections when the manager is running")

        entry = self.Entry(connection, name=name)

        self._entries_by_name[name].append(entry)

    async def run(self, *, consumer, supervisor, log):
        """Runs the communication manager in a separate task, using the
        given supervisor function to ensure that the connections associated to
        the communication manager stay open.

        Parameters:
            consumer: a callable that will be called with a Trio ReceiveChannel_
                that will yield all the packets that are received on any of
                the managed connections. More precisely, the channel will yield
                pairs consisting of a connection name (used when they were
                registered) and another pair holding the received message and
                the address it was received from.
            supervisor: a callable that will be called with a connection
                instance and a `task` keyword argument that represents an
                async callable that will be called whenever the connection is
                opened. This signature matches the `supervise()` method of
                the application instance so you typically want to pass that
                in here.
            log: logger that will be used to log messages from the
                communication manager
        """
        try:
            self._running = True
            self.log = log
            await self._run(consumer=consumer, supervisor=supervisor)
        finally:
            self.log = None
            self._running = False

    async def send_packet(
        self,
        packet: FlockCtrlPacket,
        destination: Tuple[str, Optional[IPAddressAndPort]],
    ):
        """Requests the communication manager to send the given FlockCtrl packet
        to the given destination.

        Parameters:
            packet: the packet to send
            destination: the name of the communication channel and the address
                on that communication channel to send the packet to. `None` as
                an address means to send a broadcast packet on the given
                channel.
        """
        name, address = destination
        entries = self._entries_by_name.get(name)
        if not entries:
            raise ValueError(f"unknown communication channel: {name}")

        await entries[0].channel.send((packet, address))

    def _iter_entries(self) -> Generator["Entry", None, None]:
        for _, entries in self._entries_by_name.items():
            yield from entries

    async def _run(self, *, consumer, supervisor):
        tx_queue, rx_queue = open_memory_channel(0)
        tasks = [
            partial(
                supervisor,
                entry.connection,
                task=partial(self._run_link, entry=entry, queue=tx_queue),
            )
            for entry in self._iter_entries()
        ]
        tasks.append(partial(consumer, rx_queue))

        async with tx_queue, rx_queue:
            await wait_all(*tasks)

    async def _run_link(self, connection, *, entry, queue):
        address = getattr(connection, "address")
        address = format_socket_address(address) if address else None
        has_error = False

        try:
            if address:
                self.log.info(f"Connection at {address} up and running.")

            entry.channel = create_flockctrl_udp_message_channel(connection, self.log)
            async for message in entry.channel:
                await queue.send((entry.name, message))

        except Exception as ex:
            has_error = True
            self.log.exception(ex)
            if address:
                self.log.warn(f"Connection at {address} down, trying to reopen.")

        finally:
            entry.channel = None
            if address and not has_error:
                self.log.info(f"Connection at {address} closed.")


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
    stdin_stream, stdout_stream, stderr_stream = ssh.exec_command(
        command, timeout=timeout
    )
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
    with open_ssh(address, username="root") as ssh:
        scp = open_scp(ssh)
        scp.putfo(BytesIO(raw_data), f"/home/tamas/.flockctrl/inbox/{name}.mission")
        stdout, stderr, exit_code = execute_ssh_command(
            ssh, "systemctl restart flockctrl"
        )

        if exit_code != 0:
            raise ValueError("Failed to restart flockctrl process")
