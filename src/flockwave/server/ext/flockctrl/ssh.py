"""Helper functions for handling sftp connections to a flockctrl-based
drone.
"""

from paramiko import SSHClient
from paramiko import SFTPClient
from paramiko.client import AutoAddPolicy
from scp import SCPClient
from typing import Optional, Tuple, Union

__all__ = ("create_ssh_client", "open_scp", "open_sftp", "open_ssh")


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


open_ssh = create_ssh_client


def open_scp(address: AddressLike, *args, **kwds) -> SCPClient:
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
