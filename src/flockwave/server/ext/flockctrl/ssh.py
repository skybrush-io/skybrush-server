"""Helper functions for handling sftp connections to a flockctrl-based
drone.
"""

from paramiko import SSHClient
from paramiko.client import AutoAddPolicy
from paramiko.sftp import SFTPClient
from scp import SCPClient


def create_ssh_client() -> SSHClient:
    """Creates an SSH client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).
    """
    ssh = SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(AutoAddPolicy())
    return ssh


def create_scp_client() -> SCPClient:
    """Creates an SCP client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).
    """
    ssh = create_ssh_client()
    scp = SCPClient(ssh.get_transport())
    return scp


def create_sftp_client() -> SFTPClient:
    """Creates an SFTP client that is suitable for connecting to a
    flockctrl-based drone in an unsupervised manner (assuming that the
    appropriate public key is installed on the drone).
    """
    ssh = create_ssh_client()
    return ssh.open_sftp()
