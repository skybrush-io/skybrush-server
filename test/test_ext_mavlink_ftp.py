"""Tests for the MAVFTP implementation of the MAVLink extension."""

from base64 import b64encode
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass
from errno import EEXIST, ENOENT
from types import SimpleNamespace
from unittest.mock import Mock

from pytest import fixture, mark, raises
from trio import CancelScope
from trio.lowlevel import checkpoint

from flockwave.server.errors import CommandInvocationError
from flockwave.server.ext.mavlink.driver import MAVLinkDriver, MAVLinkUAV
from flockwave.server.ext.mavlink.ftp import (
    MAVFTP,
    MAVFTPErrorCode,
    MAVFTPMessage,
    MAVFTPOpCode,
    OperationNotAcknowledgedError,
)
from flockwave.server.model.commands import Progress
from flockwave.server.show.utils import crc32_mavftp as crc32


@dataclass
class FakeMAVLinkMessage:
    payload: bytes


class FakeMAVFTPServer:
    """Minimal in-memory MAVFTP server that mimics a real filesystem in the
    sense that files and directories can only be created if their parent
    directory exists.

    Like ArduPilot, the server refuses to create a file while another file is
    still open.
    """

    def __init__(self, *, report_exists_as_errno: bool = False):
        self.dirs: set[str] = {""}
        self.files: dict[str, bytearray] = {}
        self.requests: list[tuple[int, str]] = []
        self.opcodes: list[int] = []
        self.on_request: Callable[[MAVFTPMessage], None] | None = None
        self._sessions: dict[int, str] = {}
        self._next_session_id = 1
        self._report_exists_as_errno = report_exists_as_errno

    async def __call__(
        self, packet, wait_for_response=None, target=None
    ) -> FakeMAVLinkMessage:
        _, kwds = packet
        payload = kwds["payload"]
        seq_no = payload[0] + (payload[1] << 8)
        request = MAVFTPMessage.decode(payload)

        # A real link yields to the event loop, and cancellation can strike here
        await checkpoint()
        if self.on_request:
            self.on_request(request)

        reply = self._handle(request)
        return FakeMAVLinkMessage(payload=reply.encode((seq_no + 1) % 65536))

    @staticmethod
    def _normalize(path: bytes) -> str:
        path_str = path.decode("utf-8").strip("/")
        return "" if path_str == "." else path_str

    @staticmethod
    def _parent_of(path: str) -> str:
        return path.rpartition("/")[0]

    def _exists_error(self) -> MAVFTPMessage:
        if self._report_exists_as_errno:
            return self._nak(MAVFTPErrorCode.FAIL_ERRNO, EEXIST)
        else:
            return self._nak(MAVFTPErrorCode.FILE_EXISTS)

    @staticmethod
    def _ack(**kwds) -> MAVFTPMessage:
        return MAVFTPMessage(MAVFTPOpCode.ACK, **kwds)

    @staticmethod
    def _nak(code: int, errno: int | None = None) -> MAVFTPMessage:
        data = bytes([code]) if errno is None else bytes([code, errno])
        return MAVFTPMessage(MAVFTPOpCode.NAK, data=data)

    def _handle(self, request: MAVFTPMessage) -> MAVFTPMessage:
        opcode = request.opcode
        path = self._normalize(request.data)
        self.opcodes.append(opcode)

        if opcode in (MAVFTPOpCode.CREATE_DIRECTORY, MAVFTPOpCode.CREATE_FILE):
            self.requests.append((opcode, path))

        if opcode == MAVFTPOpCode.CREATE_DIRECTORY:
            if path in self.dirs or path in self.files:
                return self._exists_error()
            if self._parent_of(path) not in self.dirs:
                return self._nak(MAVFTPErrorCode.FAIL_ERRNO, ENOENT)
            self.dirs.add(path)
            return self._ack()

        elif opcode == MAVFTPOpCode.CREATE_FILE:
            if self._sessions:
                return self._nak(MAVFTPErrorCode.FAIL)
            if path in self.dirs or self._parent_of(path) not in self.dirs:
                return self._nak(MAVFTPErrorCode.FAIL_ERRNO, ENOENT)
            self.files[path] = bytearray()
            session_id = self._next_session_id
            self._next_session_id += 1
            self._sessions[session_id] = path
            return self._ack(session_id=session_id)

        elif opcode == MAVFTPOpCode.WRITE_FILE:
            content = self.files[self._sessions[request.session_id]]
            content[request.offset : request.offset + len(request.data)] = request.data
            return self._ack(session_id=request.session_id)

        elif opcode == MAVFTPOpCode.CALC_FILE_CRC32:
            if path not in self.files:
                return self._nak(MAVFTPErrorCode.FILE_NOT_FOUND)
            return self._ack(data=crc32(bytes(self.files[path])).to_bytes(4, "little"))

        elif opcode == MAVFTPOpCode.TERMINATE_SESSION:
            self._sessions.pop(request.session_id, None)
            return self._ack()

        elif opcode == MAVFTPOpCode.RESET_SESSIONS:
            self._sessions.clear()
            return self._ack()

        else:
            return self._nak(MAVFTPErrorCode.UNKNOWN_COMMAND)


@fixture
def server() -> FakeMAVFTPServer:
    return FakeMAVFTPServer()


@fixture
def ftp(server: FakeMAVFTPServer) -> MAVFTP:
    return MAVFTP(server)  # ty:ignore[invalid-argument-type]


@mark.parametrize(
    ("path", "expected"),
    [
        ("/APM/scripts/foo.lua", ["/APM", "/APM/scripts"]),
        ("APM/scripts/foo.lua", ["/APM", "/APM/scripts"]),
        ("/APM", []),
        ("/", []),
        ("/a/b/c/d", ["/a", "/a/b", "/a/b/c"]),
    ],
)
def test_parents_of_resolved_path(ftp: MAVFTP, path: str, expected: list[str]):
    resolved = ftp._resolve(path)
    parents = list(ftp._parents_of(resolved))
    assert parents == expected

    # Parents must survive a second round of resolution unchanged
    assert [ftp._resolve(p) for p in parents] == [p[1:].encode() for p in expected]


async def test_mkdir_with_parents(ftp: MAVFTP, server: FakeMAVFTPServer):
    await ftp.mkdir("/APM/scripts", parents=True)
    assert {"APM", "APM/scripts"} <= server.dirs

    # Creating it again must not fail either
    await ftp.mkdir("/APM/scripts", parents=True)


async def test_mkdir_existing_without_exist_ok(ftp: MAVFTP, server: FakeMAVFTPServer):
    await ftp.mkdir("/APM")
    with raises(OperationNotAcknowledgedError):
        await ftp.mkdir("/APM")


async def test_mkdir_exist_ok_with_errno_style_error():
    server = FakeMAVFTPServer(report_exists_as_errno=True)
    ftp = MAVFTP(server)  # ty:ignore[invalid-argument-type]

    await ftp.mkdir("/APM/scripts", parents=True)
    await ftp.mkdir("/APM/scripts", parents=True)
    assert {"APM", "APM/scripts"} <= server.dirs


async def test_put_with_parents(ftp: MAVFTP, server: FakeMAVFTPServer):
    data = b"-- test script\n" * 100  # spans multiple chunks
    await ftp.put(data, "/APM/scripts/foo.lua", parents=True)

    assert server.files["APM/scripts/foo.lua"] == data
    assert "APM/scripts/foo.lua" not in server.dirs
    assert server.requests == [
        (MAVFTPOpCode.CREATE_DIRECTORY, "APM"),
        (MAVFTPOpCode.CREATE_DIRECTORY, "APM/scripts"),
        (MAVFTPOpCode.CREATE_FILE, "APM/scripts/foo.lua"),
    ]


async def test_put_with_parents_existing_dirs(ftp: MAVFTP, server: FakeMAVFTPServer):
    server.dirs |= {"APM", "APM/scripts"}
    await ftp.put(b"print('hello')", "/APM/scripts/foo.lua", parents=True)
    assert server.files["APM/scripts/foo.lua"] == b"print('hello')"


async def test_put_with_parents_into_root(ftp: MAVFTP, server: FakeMAVFTPServer):
    await ftp.put(b"data", "/foo.txt", parents=True)
    assert server.files["foo.txt"] == b"data"
    assert server.requests == [(MAVFTPOpCode.CREATE_FILE, "foo.txt")]


async def test_put_without_parents_fails_on_missing_dir(ftp: MAVFTP):
    with raises(OperationNotAcknowledgedError):
        await ftp.put(b"data", "/APM/scripts/foo.lua")


async def test_put_cancelled_mid_transfer_releases_file_handle(
    server: FakeMAVFTPServer,
):
    data = b"-- test script\n" * 100  # spans multiple chunks

    with CancelScope() as scope:

        def cancel_on_first_write(request: MAVFTPMessage) -> None:
            if request.opcode == MAVFTPOpCode.WRITE_FILE:
                scope.cancel()

        server.on_request = cancel_on_first_write

        # Same setup as in MAVLinkDriver.upload_show()
        async with aclosing(MAVFTP(server)) as ftp:  # ty:ignore[invalid-argument-type]
            await ftp.put(data, "/foo.txt")

    assert scope.cancelled_caught
    server.on_request = None

    # The cancelled upload must still close its file handle on the drone
    assert MAVFTPOpCode.RESET_SESSIONS in server.opcodes

    # The drone must accept the next upload
    async with aclosing(MAVFTP(server)) as ftp:  # ty:ignore[invalid-argument-type]
        await ftp.put(data, "/foo.txt")
    assert server.files["foo.txt"] == data


async def test_uav_upload_file(server: FakeMAVFTPServer):
    # MAVFTP.for_uav() needs nothing but uav.driver.send_packet()
    uav = SimpleNamespace(driver=SimpleNamespace(send_packet=server))
    data = b"-- test script\n" * 100

    events = [
        event
        async for event in MAVLinkUAV.upload_file(
            uav,  # ty:ignore[invalid-argument-type]
            data,
            "/APM/scripts/foo.lua",
        )
    ]

    assert server.files["APM/scripts/foo.lua"] == data
    percentages = [event.percentage for event in events]
    assert all(percentage is not None for percentage in percentages)
    percentages = [percentage or 0 for percentage in percentages]
    assert percentages[0] == 0
    assert percentages[-1] == 100
    assert percentages == sorted(percentages)

    # Uploading again overwrites the file
    async for _ in MAVLinkUAV.upload_file(
        uav,  # ty:ignore[invalid-argument-type]
        b"print('v2')",
        "/APM/scripts/foo.lua",
    ):
        pass

    assert server.files["APM/scripts/foo.lua"] == b"print('v2')"


@fixture
def driver_and_uav(server: FakeMAVFTPServer):
    driver = SimpleNamespace(log=Mock(), send_packet=server)
    uav = SimpleNamespace(id="42", driver=driver)
    uav.upload_file = lambda *args: MAVLinkUAV.upload_file(uav, *args)  # ty:ignore[invalid-argument-type]
    return driver, uav


async def test_file_upload_command(server: FakeMAVFTPServer, driver_and_uav):
    driver, uav = driver_and_uav
    data = b"-- test script\n" * 100

    events = [
        event
        async for event in MAVLinkDriver.handle_command___file_upload(
            driver,
            uav,
            path="/APM/scripts/foo.lua",
            data=b64encode(data).decode("ascii"),
        )
    ]

    assert server.files["APM/scripts/foo.lua"] == data
    assert events[-1] == f"Uploaded {len(data)} bytes to /APM/scripts/foo.lua"
    progress = events[:-1]
    assert all(isinstance(event, Progress) for event in progress)
    last_progress = progress[-1]
    assert isinstance(last_progress, Progress)
    assert last_progress.percentage == 100


async def test_file_upload_command_invalid_args(
    server: FakeMAVFTPServer, driver_and_uav
):
    driver, uav = driver_and_uav

    with raises(CommandInvocationError):
        async for _ in MAVLinkDriver.handle_command___file_upload(
            driver,
            uav,
            path="/APM/../foo.lua",
            data="",
        ):
            pass

    assert server.requests == []


async def test_file_upload_command_failure(server: FakeMAVFTPServer, driver_and_uav):
    driver, uav = driver_and_uav

    # A file where a directory should be makes the upload fail
    server.files["APM"] = bytearray()

    with raises(OperationNotAcknowledgedError):
        async for _ in MAVLinkDriver.handle_command___file_upload(
            driver,
            uav,
            path="/APM/scripts/foo.lua",
            data=b64encode(b"data").decode("ascii"),
        ):
            pass

    driver.log.error.assert_called_once()
    assert driver.log.error.call_args.kwargs["extra"] == {"id": "42"}


@fixture
def driver_and_uav_for_crc(server: FakeMAVFTPServer):
    driver = SimpleNamespace(log=Mock(), send_packet=server)
    uav = SimpleNamespace(id="42", driver=driver)
    uav.get_file_crc32 = lambda *args: MAVLinkUAV.get_file_crc32(uav, *args)  # ty:ignore[invalid-argument-type]
    return driver, uav


async def test_file_crc_command(server: FakeMAVFTPServer, driver_and_uav_for_crc):
    driver, uav = driver_and_uav_for_crc
    data = b"-- test script\n" * 100
    server.dirs.add("collmot")
    server.files["collmot/show.skyb"] = bytearray(data)

    result = await MAVLinkDriver.handle_command___file_crc(
        driver, uav, path="/collmot/show.skyb"
    )

    assert result == f"{crc32(data):08x}"
    assert len(result) == 8
    assert MAVFTPOpCode.RESET_SESSIONS in server.opcodes


async def test_file_crc_command_pads_with_zeros(
    server: FakeMAVFTPServer, driver_and_uav_for_crc
):
    driver, uav = driver_and_uav_for_crc
    server.files["empty.txt"] = bytearray()

    result = await MAVLinkDriver.handle_command___file_crc(
        driver, uav, path="/empty.txt"
    )

    assert result == f"{crc32(b''):08x}"
    assert len(result) == 8


async def test_file_crc_command_invalid_args(
    server: FakeMAVFTPServer, driver_and_uav_for_crc
):
    driver, uav = driver_and_uav_for_crc

    with raises(CommandInvocationError):
        await MAVLinkDriver.handle_command___file_crc(
            driver, uav, path="/collmot/../show.skyb"
        )

    assert server.opcodes == []


async def test_file_crc_command_missing_file(
    server: FakeMAVFTPServer, driver_and_uav_for_crc
):
    driver, uav = driver_and_uav_for_crc

    with raises(OperationNotAcknowledgedError) as ex:
        await MAVLinkDriver.handle_command___file_crc(
            driver, uav, path="/collmot/show.skyb"
        )

    assert ex.value.code == MAVFTPErrorCode.FILE_NOT_FOUND
    driver.log.error.assert_called_once()
    assert driver.log.error.call_args.kwargs["extra"] == {"id": "42"}
