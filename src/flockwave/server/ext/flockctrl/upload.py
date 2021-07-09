import trio_parallel

from datetime import datetime
from io import BytesIO
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
    SSHException,
)
from scp import SCPException
from trio import CapacityLimiter, to_thread
from typing import Tuple, Union

from flockwave.server.ext.flockctrl.mission import validate_mission_data

__all__ = ("upload_mission",)


#: Type specification for addresses used in this module
AddressLike = Union[str, Tuple[str, int]]

#: Maximum number of concurrent upload tasks
MAX_UPLOAD_TASKS = 5

#: Global capacity limiter for concurrent upload tasks
capacity_limiter = CapacityLimiter(MAX_UPLOAD_TASKS)


async def upload_mission_in_worker_thread(data: bytes, address: AddressLike) -> None:
    """Mission upload implementation that delegates the actual communication
    to a worker thread.

    Parameters:
        raw_data: the raw data to upload. It must be an in-memory mission ZIP
            file. Some basic validity checks will be performed on it before
            attempting the upload.
        address: the network address of the UAV, either as a hostname or as a
            tuple consisting of a hostname and a port
    """
    validate_mission_data(data)

    # This method is problematic because the thread would need to be
    # interruptible, otherwise we are blocking one entry in the CapacityLimiter
    # even if the upstream request timed out. However, Paramiko provides no way
    # to interrupt an operation reliably, and we cannot kill arbitrary threads.
    await to_thread.run_sync(
        _upload_mission_blocking,
        data,
        address,
        cancellable=True,
        limiter=capacity_limiter,
    )


async def upload_mission_in_subprocess(data: bytes, address: AddressLike) -> None:
    """Mission upload implementation that delegates the actual communication
    to a subprocess.

    Parameters:
        raw_data: the raw data to upload. It must be an in-memory mission ZIP
            file. Some basic validity checks will be performed on it before
            attempting the upload.
        address: the network address of the UAV, either as a hostname or as a
            tuple consisting of a hostname and a port
    """
    validate_mission_data(data)

    await trio_parallel.run_sync(
        _upload_mission_blocking,
        data,
        address,
        cancellable=True,
        limiter=capacity_limiter,
    )


def _upload_mission_blocking(raw_data: bytes, address: AddressLike) -> None:
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
    from .ssh import execute_ssh_command, open_scp, open_ssh

    name = (
        datetime.now()
        .replace(microsecond=0)
        .isoformat()
        .replace(":", "")
        .replace("-", "")
    )

    try:
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
    except AuthenticationException:
        raise RuntimeError("SSH authentication failed") from None
    except NoValidConnectionsError:
        raise RuntimeError("Failed to establish SSH connection") from None
    except SSHException as ex:
        raise RuntimeError(f"SSH exception: {str(ex)}") from None
    except SCPException as ex:
        raise RuntimeError(f"SCP exception: {str(ex)}") from None
    except EOFError:
        raise RuntimeError(
            "Unexpected end of file error while communicating over SSH"
        ) from None
    except OSError as ex:
        raise RuntimeError(
            f"OS error while communicating over SSH ({ex.strerror})"
        ) from None
    if exit_code != 0:
        raise RuntimeError(
            f"Failed to restart flockctrl process, exit code = {exit_code}"
        )


upload_mission = upload_mission_in_subprocess
