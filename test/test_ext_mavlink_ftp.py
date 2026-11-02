"""Tests for the MAVFTP implementation of the MAVLink extension."""

from dataclasses import dataclass
from errno import EEXIST, ENOENT

from pytest import fixture, mark, raises

from flockwave.server.ext.mavlink.ftp import (
    MAVFTP,
    MAVFTPErrorCode,
    MAVFTPMessage,
    MAVFTPOpCode,
    OperationNotAcknowledgedError,
)
from flockwave.server.show.utils import crc32_mavftp as crc32


@dataclass
class FakeMAVLinkMessage:
    payload: bytes


class FakeMAVFTPServer:
    """Minimal in-memory MAVFTP server that mimics a real filesystem in the
    sense that files and directories can only be created if their parent
    directory exists.
    """

    def __init__(self, *, report_exists_as_errno: bool = False):
        self.dirs: set[str] = {""}
        self.files: dict[str, bytearray] = {}
        self.requests: list[tuple[int, str]] = []
        self._sessions: dict[int, str] = {}
        self._next_session_id = 1
        self._report_exists_as_errno = report_exists_as_errno

    async def __call__(self, packet, wait_for_response=None) -> FakeMAVLinkMessage:
        _, kwds = packet
        payload = kwds["payload"]
        seq_no = payload[0] + (payload[1] << 8)
        request = MAVFTPMessage.decode(payload)
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
