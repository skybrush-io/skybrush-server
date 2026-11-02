"""Helper functions for uploading arbitrary files to MAVLink-based UAVs."""

from base64 import b64decode
from binascii import Error as BinasciiError
from pathlib import PurePosixPath
from typing import Any

from flockwave.server.errors import CommandInvocationError

__all__ = (
    "MAX_FILE_UPLOAD_SIZE",
    "decode_file_upload_data",
    "validate_remote_file_path",
)


MAX_FILE_UPLOAD_SIZE = 256 * 1024
"""Maximum size of a file that can be uploaded to a UAV, in bytes."""


def decode_file_upload_data(data: Any) -> bytes:
    """Decodes the base64-encoded contents of a file upload request.

    Args:
        data: the base64-encoded file contents, as received from the client

    Returns:
        the decoded file contents

    Raises:
        CommandInvocationError: if the data is not valid base64 or the decoded
            file is too large
    """
    if not isinstance(data, str):
        raise CommandInvocationError("File contents must be a base64-encoded string")

    try:
        result = b64decode(data, validate=True)
    except BinasciiError:
        raise CommandInvocationError("File contents are not valid base64") from None

    if len(result) > MAX_FILE_UPLOAD_SIZE:
        raise CommandInvocationError(
            f"File too large; maximum size is {MAX_FILE_UPLOAD_SIZE} bytes"
        )

    return result


def validate_remote_file_path(path: Any) -> str:
    """Validates the remote path of a file upload request.

    The path must be absolute, must point to a file, must not contain ``..``
    components and must not refer to virtual files of the autopilot (whose
    names start with ``@``) as these need special handling.

    Args:
        path: the path to validate, as received from the client

    Returns:
        the normalized path

    Raises:
        CommandInvocationError: if the path is not acceptable
    """
    if not isinstance(path, str) or not path:
        raise CommandInvocationError("Remote path must be a non-empty string")

    if not path.startswith("/"):
        raise CommandInvocationError("Remote path must be absolute")

    if path.endswith("/"):
        raise CommandInvocationError("Remote path must point to a file")

    parts = PurePosixPath(path).parts[1:]
    if not parts:
        raise CommandInvocationError("Remote path must point to a file")

    if ".." in parts:
        raise CommandInvocationError("Remote path must not contain '..'")

    if parts[0].startswith("@"):
        raise CommandInvocationError("Uploading virtual files is not supported")

    return "/" + "/".join(parts)
