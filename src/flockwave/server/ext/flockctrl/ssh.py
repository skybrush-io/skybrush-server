"""Helper functions for handling sftp connections to a flockctrl-based
drone.
"""

from paramiko import SSHClient
from paramiko import SFTPClient
from paramiko.client import AutoAddPolicy
from select import select
from scp import SCPClient
from typing import Optional, Tuple, Union

__all__ = (
    "create_ssh_client",
    "execute_ssh_command",
    "open_scp",
    "open_sftp",
    "open_ssh",
)


#: Type specification for addresses accepted by functions in this module
AddressLike = Union[str, Tuple[str, int]]


def create_ssh_client(
    address: Optional[AddressLike] = None, *args, **kwds
) -> SSHClient:
    """Creates an SSH client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).

    Additional positional and keyword arguments are forwarded to the underlying
    `SSHClient.connect()` call.

    Parameters:
        address: an optional address to connect the client to. You may use a
            single hostname or a hostname-port pair in a tuple. `None` means to
            return an unconnected client.
    """
    ssh = SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(AutoAddPolicy())

    if address is not None:
        if isinstance(address, str):
            host, port = address, 22
        else:
            host, port = address
        ssh.connect(host, port, *args, **kwds)

    return ssh


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


open_ssh = create_ssh_client


def open_scp(address: Union[AddressLike, SSHClient], *args, **kwds) -> SCPClient:
    """Creates an SCP client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).

    Additional positional and keyword arguments are forwarded to the underlying
    `SSHClient.connect()` call.

    Parameters:
        address: an address to connect the client to, or an existing SSH client
            to use. When specified as an address, you may use a single hostname
            or a hostname-port pair in a tuple.
    """
    if isinstance(address, SSHClient):
        ssh = address
    else:
        ssh = create_ssh_client(address, *args, **kwds)
    scp = SCPClient(ssh.get_transport())
    return scp


def open_sftp(address: Union[AddressLike, SSHClient], *args, **kwds) -> SFTPClient:
    """Creates an SFTP client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).

    Additional positional and keyword arguments are forwarded to the underlying
    `SSHClient.connect()` call.

    Parameters:
        address: an address to connect the client to, or an existing SSH client
            to use. When specified as an address, you may use a single hostname
            or a hostname-port pair in a tuple.
    """
    if isinstance(address, SSHClient):
        ssh = address
    else:
        ssh = create_ssh_client(address, *args, **kwds)
    return ssh.open_sftp()
