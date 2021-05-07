"""Implementation of the MAVFTP protocol on top of a MAVLink connection."""

from contextlib import asynccontextmanager
from crcmod import mkCrcFun as make_crc_function
from dataclasses import dataclass
from enum import Enum, IntEnum
from functools import partial
from io import BytesIO
from itertools import cycle, islice
from pathlib import PurePosixPath
from random import randint
from struct import Struct
from trio import fail_after, move_on_after, TooSlowError, wrap_file
from typing import Awaitable, Callable, Iterable, Optional, Union

from flockwave.concurrency import aclosing

from .types import MAVLinkMessage, spec

__all__ = ("MAVFTP",)


#: Type specification for FTP paths that are accepted by MAVFTP
FTPPath = Union[str, bytes]

#: CRC32 function used by ArduPilot's MAVFTP implementation
crc32 = make_crc_function(0x104C11DB7, initCrc=0, rev=True, xorOut=0)

#: Maximum number of bytes allowed in a single read/write operation
_MAVFTP_CHUNK_SIZE = 239


class MAVFTPOpCode(IntEnum):
    """Opcodes for the MAVFTP sub-protocol of MAVLink."""

    NONE = 0
    TERMINATE_SESSION = 1
    RESET_SESSIONS = 2
    LIST_DIRECTORY = 3
    OPEN_FILE_RO = 4
    READ_FILE = 5
    CREATE_FILE = 6
    WRITE_FILE = 7
    REMOVE_FILE = 8
    CREATE_DIRECTORY = 9
    REMOVE_DIRECTORY = 10
    OPEN_FILE_WO = 11
    TRUNCATE_FILE = 12
    RENAME = 13
    CALC_FILE_CRC32 = 14
    BURST_READ_FILE = 15
    ACK = 128
    NAK = 129


_mavftp_error_codes = {
    0: "No error",
    1: "FTP operation failed",
    2: "FTP operation failed",
    3: "Invalid payload size",
    4: "Session is not open",
    5: "All available sessions are already in use",
    6: "End of file or directory listing",
    7: "Unknown command",
    8: "File or directory already exists",
    9: "File or directory is write protected",
    10: "File or directory not found",
}


class MAVFTPErrorCode(IntEnum):
    """Error codes used in NAK messages of the MAVFTP protocol."""

    NONE = 0
    FAIL = 1
    FAIL_ERRNO = 2
    INVALID_DATA_SIZE = 3
    INVALID_SESSION = 4
    NO_SESSIONS_AVAILABLE = 5
    EOF = 6
    UNKNOWN_COMMAND = 7
    FILE_EXISTS = 8
    FILE_PROTECTED = 9
    FILE_NOT_FOUND = 10

    def to_string(self, errno: Optional[int] = None) -> str:
        result = _mavftp_error_codes.get(int(self)) or f"Unknown error code {int(self)}"
        if errno is not None:
            result = f"{result} (errno = {errno})"
        return result


#: Struct representing the format of the payload of a MAVFTP message
_MAVFTPMessageStruct = Struct("<HBBBBBxI")


class ListingEntryType(Enum):
    """Enum representing the file listing entry types supported by the MAVFTP
    protocol.
    """

    FILE = "F"
    DIRECTORY = "D"
    SKIP = "S"


@dataclass
class ListingEntry:
    """Data class representing a single entry in a file listing returned by
    a MAVFTP listing command.
    """

    name: str
    type: ListingEntryType
    size: int

    @classmethod
    def decode(cls, data: bytes):
        if not data:
            raise ValueError("Data must not be empty")

        head, sep, tail = data.partition(b"\t")
        size = int(tail) if tail else 0

        return cls(
            type=ListingEntryType(chr(head[0])),
            name=head[1:].decode("utf-8"),
            size=size,
        )

    @property
    def hidden(self) -> bool:
        return not self.name or self.name.startswith(".")

    @property
    def is_dir(self) -> bool:
        return self.type == ListingEntryType.DIRECTORY


class MAVFTPError(RuntimeError):
    """Base class for MAVFTP-related errors."""

    pass


class OperationNotAcknowledgedError(MAVFTPError):
    def __init__(self, code: int, errno: Optional[int] = None):
        super().__init__(MAVFTPErrorCode.to_string(code, errno))
        self.code = code


class SequenceNumberMismatch(MAVFTPError):
    """Exception raised by MAVFTPMessage.decode() if the sequence ID of the
    received packet does not match the one we expect.
    """

    pass


@dataclass
class MAVFTPMessage:
    """Specification of a single MAVFTP message."""

    opcode: int
    session_id: int = 0
    offset: int = 0
    data: bytes = b""
    size: Optional[int] = None

    @classmethod
    def decode(cls, payload: bytes, expected_seq_no: Optional[int] = None):
        """Constructs a MAVFTP message by decoding the payload of a MAVLink
        FILE_TRANSFER_PROTOCOL message.

        Parameters:
            payload: the payload to decode
            expected_seq_no: the sequence number to expect in the payload

        Raises:
            SequenceNumberMismatch: when the expected sequence number does not match the
                one in the decoded message
        """
        nbytes = _MAVFTPMessageStruct.size

        head, data = payload[:nbytes], payload[nbytes:]
        (
            seq_no,
            session_id,
            opcode,
            size,
            req_opcode,
            burst_complete,
            offset,
        ) = _MAVFTPMessageStruct.unpack(bytes(head))

        if expected_seq_no is None or seq_no == expected_seq_no:
            return cls(
                opcode,
                session_id=session_id,
                offset=offset,
                data=bytes(data[:size]),
                size=size,
            )
        else:
            raise SequenceNumberMismatch()

    @classmethod
    def matches_sequence_no(cls, seq_no: int, message: MAVLinkMessage) -> bool:
        """Returns whether the payload of the given raw MAVLink
        FILE_TRANSFER_PROTOCOL message contains the given sequence number.

        Parameters:
            seq_no: the sequence number to expect in the payload
            message: the message to decode

        Returns:
            whether the message is a MAVLink FILE_TRANSFER_PROTOCOL message with
            the given expected sequence number
        """
        payload = message.payload
        return len(payload) >= _MAVFTPMessageStruct.size and (
            payload[0] + (payload[1] << 8) == seq_no
        )

    def encode(self, seq_no: int) -> bytes:
        """Encodes the message in a format that is suitable to be sent over a
        MAVLink connection, given its MAVFTP sequence number.
        """
        if len(self.data) > 251:
            raise ValueError(
                f"data too long; max length is 251 bytes, got {len(self.data)}"
            )

        size = self.size if self.size is not None else len(self.data)
        return (
            _MAVFTPMessageStruct.pack(
                seq_no, self.session_id, self.opcode, size, 0, 0, self.offset
            )
            + self.data
        )

    @property
    def error_code(self) -> int:
        """Returns the error code encapsulated in this message if it is a NAK.

        Returns:
            the error code

        Raises:
            RuntimeError: if the message is not a NAK
        """
        if self.is_nak and self.data:
            return self.data[0]
        else:
            raise RuntimeError("Message is not a NAK")

    @property
    def is_ack(self) -> bool:
        """Returns whether the message is an ACK."""
        return self.opcode == MAVFTPOpCode.ACK

    @property
    def is_nak(self) -> bool:
        """Returns whether the message is a NAK."""
        return self.opcode == MAVFTPOpCode.NAK

    def raise_error(self):
        if self.is_nak:
            errno = self.data[1] if len(self.data) >= 2 else None
            raise OperationNotAcknowledgedError(self.error_code, errno=errno)
        else:
            raise ValueError("Message is not an error")


class MAVFTPSession:
    """Class representing a single reading or writing session over a MAVFTP
    connection.

    Do not create instances of this class directly unless you know what you are
    doing; typically, the MAVFTP class uses this internally.
    """

    def __init__(
        self,
        session_id: int,
        sender: Callable[[MAVFTPMessage], Awaitable[MAVFTPMessage]],
    ):
        """Constructor.

        Parameters:
            session_id: the session ID to use
            sender: callable that can be called with a single MAVFTPMessage to
                send it and wait for the reply from the PixHawk
        """
        self._closed = False
        self._closing = False

        self._session_id = session_id
        self._sender = sender

    async def aclose(self) -> None:
        """Closes the session. The session object should not be used after
        calling this method.
        """
        if self._closed or self._closing:
            return

        self._closing = True
        try:
            await self._aclose()
            self._closed = True
        finally:
            self._closing = False

    async def _aclose(self) -> None:
        message = MAVFTPMessage(
            MAVFTPOpCode.TERMINATE_SESSION,
            session_id=self._session_id,
        )
        await self._sender(message)

    def _ensure_open(self) -> None:
        """Ensures that the session is open.

        Raises:
            RuntimeError: if the session is already closed
        """
        if self._closed:
            raise RuntimeError("Session is already closed")
        elif self._closing:
            raise RuntimeError("Session is already being closed")

    async def read(self, size: int = _MAVFTP_CHUNK_SIZE, offset: int = 0) -> bytes:
        """Reads some data from the session at the given offset.

        Parameters:
            size: maximum number of bytes to read
            offset: offset to read from

        Returns:
            the bytes that were read or an empty byte array if EOF was
            reached or if the incoming size was zero
        """
        if size < 0:
            raise ValueError("size must be non-negative")
        elif size == 0:
            return b""
        elif size > _MAVFTP_CHUNK_SIZE:
            raise ValueError(
                f"chunk size too large, maximum allowed is {_MAVFTP_CHUNK_SIZE} bytes"
            )

        self._ensure_open()
        message = MAVFTPMessage(
            MAVFTPOpCode.READ_FILE,
            session_id=self._session_id,
            offset=offset,
            size=size,
        )
        reply = await self._sender(message)
        return reply.data

    async def write(self, data: bytes, offset: int = 0) -> int:
        """Writes the given data to the session at the given offset.

        Parameters:
            data: the data to write
            offset: the byte offset to write the data at

        Returns:
            the number of bytes written
        """
        self._ensure_open()
        message = MAVFTPMessage(
            MAVFTPOpCode.WRITE_FILE,
            session_id=self._session_id,
            offset=offset,
            data=data,
        )
        await self._sender(message)
        return len(data)


class MAVFTP:
    """A single MAVFTP connection to a PixHawk over a MAVLink connection."""

    @classmethod
    def for_uav(cls, uav):
        """Constructs a MAVFTP connection object to the given UAV."""
        sender = partial(uav.driver.send_packet, target=uav)
        return cls(sender)

    def __init__(self, sender: Callable):
        """Constructor."""
        self._closed = False
        self._closing = False

        self._path = PurePosixPath("/")
        self._seq = islice(cycle(range(65536)), randint(0, 65535), None)
        self._sessions = {}
        self._sender = sender

    async def aclose(self) -> None:
        """Closes the MAVFTP connection and instructs the PixHawk to close
        all open file handles.

        The connection object should not be used after this operation.
        """
        if self._closed or self._closing:
            return

        self._closing = True
        try:
            await self._aclose()
            self._closed = True
        finally:
            self._closing = False

    async def _aclose(self) -> None:
        message = MAVFTPMessage(MAVFTPOpCode.RESET_SESSIONS)
        with move_on_after(10):
            await self._send_and_wait(message)

    async def crc32(self, path: FTPPath):
        """Calculates the unsigned CRC32 checksum of a file on the PixHawk."""
        path = self._resolve(path)
        message = MAVFTPMessage(MAVFTPOpCode.CALC_FILE_CRC32, data=path)
        reply = await self._send_and_wait(message)
        return int.from_bytes(reply.data, byteorder="little")

    async def get(self, remote_path: FTPPath, fp=None) -> Optional[bytes]:
        """Downloads a file at a given remote path.

        Parameters:
            path: remote path where the file is located
            fp: optional async file-like object to write the downloaded file to.
                When it is None, the file will be downloaded into memory and
                returned

        Returns:
            the contents of the downloaded file if `fp` was not `None`, `None`
            otherwise
        """
        remote_path = self._resolve(remote_path)

        if fp is None:
            buffer = BytesIO()
            await self.get(remote_path, wrap_file(buffer))
            return buffer.getvalue()

        message = MAVFTPMessage(MAVFTPOpCode.OPEN_FILE_RO, data=remote_path)
        reply = await self._send_and_wait(message)

        async with self._open_session(reply.session_id) as session:
            offset = 0
            got_eof = False
            while not got_eof:
                bytes_requested = _MAVFTP_CHUNK_SIZE
                try:
                    chunk = await session.read(offset=offset, size=bytes_requested)
                except OperationNotAcknowledgedError as ex:
                    if ex.code == MAVFTPErrorCode.EOF:
                        chunk = b""
                    else:
                        raise

                if chunk:
                    await fp.write(chunk)
                    offset += len(chunk)
                else:
                    got_eof = True

                if len(chunk) < bytes_requested:
                    got_eof = True

    async def ls(self, path: FTPPath = ".") -> Iterable[ListingEntry]:
        """Lists the contents of a directory on the PixHawk.

        Yields:
            ListingEntryType: one entry for each file or directory in the given
                path on the PixHawk
        """
        path = self._resolve(path)
        offset = 0

        while True:
            message = MAVFTPMessage(
                MAVFTPOpCode.LIST_DIRECTORY, data=path, offset=offset
            )
            reply = await self._send_and_wait(message, allow_nak=True)
            if reply.is_ack:
                for part in reply.data.split(b"\x00"):
                    if part:
                        yield ListingEntry.decode(part)
                        offset += 1
            elif reply.error_code == MAVFTPErrorCode.EOF:
                break
            else:
                reply.raise_error()

    async def mkdir(self, path: FTPPath, parents: bool = False, exist_ok: bool = False):
        """Creates a directory on the PixHawk over a MAVLink connection."""
        path = self._resolve(path)

        if parents:
            exist_ok = True
            for parent in self._parents_of(path):
                await self.mkdir(parent, parents=False, exist_ok=True)

        message = MAVFTPMessage(MAVFTPOpCode.CREATE_DIRECTORY, data=b"/" + path)
        try:
            await self._send_and_wait(message)
        except OperationNotAcknowledgedError as ex:
            if ex.code == MAVFTPErrorCode.FILE_EXISTS and exist_ok:
                return
            else:
                raise

    async def put(self, fp, remote_path: FTPPath, parents: bool = False) -> None:
        """Uploads a file at a local path to the given remote path.

        Parameters:
            fp: async file-like object containing the data to be uploaded, or a
                raw bytes object
            remote_path: remote folder where the file should be uploaded
            parents: whether to create any parent directories automatically
        """
        if isinstance(fp, bytes):
            fp = wrap_file(BytesIO(fp))

        remote_path = self._resolve(remote_path)
        if parents:
            await self.mkdir(remote_path.parent(), parents=True)

        message = MAVFTPMessage(MAVFTPOpCode.CREATE_FILE, data=remote_path)
        reply = await self._send_and_wait(message)

        expected_crc = 0
        async with self._open_session(reply.session_id) as session:
            offset = 0
            while True:
                data = await fp.read(_MAVFTP_CHUNK_SIZE)
                if data:
                    offset += await session.write(data=data, offset=offset)
                    expected_crc = crc32(data, expected_crc)
                else:
                    break

        observed_crc = await self.crc32(remote_path)
        if observed_crc != expected_crc:
            raise RuntimeError(
                "CRC mismatch, expected {0:08X}, got {1:08X}".format(
                    expected_crc, observed_crc
                )
            )

    async def rm(self, path: FTPPath) -> None:
        """Removes a file at the given path in the MAVFTP session."""
        path = self._resolve(path)
        message = MAVFTPMessage(MAVFTPOpCode.REMOVE_FILE, data=path)
        await self._send_and_wait(message)

    @asynccontextmanager
    async def _open_session(self, session_id: int) -> MAVFTPSession:
        """Context manager that creates a new MAVFTP session for file uploads or
        downloads and closes the session when the context is exited.
        """
        session = MAVFTPSession(session_id, self._send_and_wait)
        async with aclosing(session):
            yield session

    def _parents_of(self, path: FTPPath) -> Iterable[FTPPath]:
        for path in reversed(PurePosixPath(path.decode("utf-8")).parents):
            yield self._to_ftp_path(path)

    def _resolve(self, name: FTPPath = ".") -> PurePosixPath:
        """Resolves a relative or absolute path name with the current path of
        this connection and returns an appropriate POSIX path object.
        """
        if isinstance(name, bytes):
            name = name.decode("utf-8")

        # This is tricky; if we let the path start with a slash, the MAVFTP
        # implementation of the ArduPilot SITL would let us "escape" the
        # directory the SITL simulator was started from, so we always strip the
        # leading slash
        return self._to_ftp_path(self._path / name)

    async def _send_and_wait(
        self,
        message: MAVFTPMessage,
        *,
        timeout: float = 0.1,
        retries: int = 600,
        allow_nak: bool = False,
    ) -> MAVFTPMessage:
        """Sends a raw FTP message over the connection and waits for the response
        from the PixHawk.

        Parameters:
            message: the MAVFTP message to send
            expected_reply: message matcher that matches messages that we expect
                from the connection as a reply to the original message
            timeout: maximum number of seconds to wait before attempting to
                re-send the message
            retries: maximum number of retries before giving up

        Returns:
            the FTP message sent by the UAV in response

        Raises:
            TooSlowError: if the UAV failed to respond either with an ACK or a
                NAK in time.
        """
        message = message.encode(next(self._seq)).ljust(251, b"\x00")
        expected_seq_no = next(self._seq)
        sender = self._sender

        while True:
            try:
                with fail_after(timeout):
                    reply = await sender(
                        spec.file_transfer_protocol(target_network=0, payload=message),
                        wait_for_response=spec.file_transfer_protocol(
                            partial(MAVFTPMessage.matches_sequence_no, expected_seq_no)
                        ),
                    )
            except TooSlowError:
                if retries > 0:
                    retries -= 1
                    continue
                else:
                    break

            reply = MAVFTPMessage.decode(reply.payload)
            if reply.is_ack:
                return reply
            elif reply.is_nak:
                if allow_nak:
                    return reply
                else:
                    reply.raise_error()
            else:
                raise RuntimeError("Received reply that is neither ACK nor NAK")

        raise TooSlowError("No response received for MAVFTP packet in time")

    def _to_ftp_path(self, posix_path: PurePosixPath) -> bytes:
        return (str(posix_path)[1:] or ".").encode("utf-8")
