"""Tests for the file upload helpers of the MAVLink extension."""

from base64 import b64encode

from pytest import mark, raises

from flockwave.server.errors import CommandInvocationError
from flockwave.server.ext.mavlink.file_upload import (
    DEFAULT_MAX_FILE_UPLOAD_SIZE,
    decode_file_upload_data,
    validate_remote_file_path,
)


@mark.parametrize(
    ("path", "expected"),
    [
        ("/APM/scripts/foo.lua", "/APM/scripts/foo.lua"),
        ("//APM//scripts/./foo.lua", "/APM/scripts/foo.lua"),
        ("/foo.txt", "/foo.txt"),
    ],
)
def test_validate_remote_file_path(path: str, expected: str):
    assert validate_remote_file_path(path) == expected


@mark.parametrize(
    "path",
    [
        None,
        42,
        "",
        "/",
        "APM/scripts/foo.lua",
        "/APM/scripts/",
        "/APM/../foo.lua",
        "/@PARAM/param.pck",
    ],
)
def test_validate_remote_file_path_invalid(path):
    with raises(CommandInvocationError):
        validate_remote_file_path(path)


def test_decode_file_upload_data_custom_max_size():
    data = b64encode(b"x" * 16).decode("ascii")
    assert decode_file_upload_data(data, max_size=16) == b"x" * 16
    with raises(CommandInvocationError):
        decode_file_upload_data(data, max_size=15)


def test_decode_file_upload_data():
    assert decode_file_upload_data(b64encode(b"hello").decode("ascii")) == b"hello"
    assert decode_file_upload_data("") == b""

    max_size = b"x" * DEFAULT_MAX_FILE_UPLOAD_SIZE
    assert decode_file_upload_data(b64encode(max_size).decode("ascii")) == max_size


@mark.parametrize(
    "data",
    [
        None,
        b"aGVsbG8=",
        "not base64!",
        "aGVsbG8",  # missing padding
        b64encode(b"x" * (DEFAULT_MAX_FILE_UPLOAD_SIZE + 1)).decode("ascii"),
    ],
)
def test_decode_file_upload_data_invalid(data):
    with raises(CommandInvocationError):
        decode_file_upload_data(data)
