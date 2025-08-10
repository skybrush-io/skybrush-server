from __future__ import annotations

from heapq import heappush, heappop
from logging import ERROR, WARNING, INFO, DEBUG
from typing import NamedTuple, Optional, Union, TYPE_CHECKING

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.log import Severity

from .enums import MAVFrame, MAVParamType, MAVState
from .types import MAVLinkMessage

if TYPE_CHECKING:
    from .driver import MAVLinkUAV

__all__ = (
    "can_communicate_infer_from_heartbeat",
    "decode_param_from_wire_representation",
    "encode_param_to_wire_representation",
    "log_id_for_uav",
    "log_id_from_message",
    "mavlink_nav_command_to_gps_coordinate",
    "mavlink_version_number_to_semver",
    "python_log_level_from_mavlink_severity",
)


_mavlink_severity_to_python_log_level = [
    ERROR,
    ERROR,
    ERROR,
    ERROR,
    WARNING,
    WARNING,
    INFO,
    DEBUG,
]

_mavlink_severity_to_flockwave_severity = [
    Severity.CRITICAL,  # MAV_SEVERITY_EMERGENCY
    Severity.ERROR,  # MAV_SEVERITY_ALERT: "Indicates error in non-critical systems"
    Severity.CRITICAL,  # MAV_SEVERITY_CRITICAL: "Indicates failure in a primary system."
    Severity.ERROR,  # MAV_SEVERITY_ERROR
    Severity.WARNING,  # MAV_SEVERITY_WARNING: "An unusual event has occurred, though not an error condition. This should be investigated for the root cause."
    Severity.WARNING,  # MAV_SEVERITY_NOTICE
    Severity.INFO,  # MAV_SEVERITY_INFO
    Severity.DEBUG,  # MAV_SEVERITY_DEBUG
]


def can_communicate_infer_from_heartbeat(message: Optional[MAVLinkMessage]) -> bool:
    """Decides whether a drone that has sent the given heartbeat message is
    likely to be able to communicate now. This function is used to distinguish
    drones in a sleep state from drones where the flight controller is alive.
    """
    system_status = getattr(message, "system_status", None)
    return (
        system_status is not None
        and system_status != MAVState.FLIGHT_TERMINATION
        and system_status != MAVState.BOOT
    )


def decode_param_from_wire_representation(
    value, type: MAVParamType
) -> Union[int, float]:
    """Decodes the given value when it is interpreted as a given MAVLink type,
    received from a MAVLink parameter retrieval command.

    This is a quirk of the MAVLink parameter protocol where the official,
    over-the-wire type of each parameter is a float, but sometimes we want to
    transfer, say 32-bit integers. In this case, the 32-bit integer
    representation is _reinterpreted_ as a float, and the resulting float value
    is sent over the wire; the other side will then _reinterpret_ it again as
    a 32-bit integer.

    See `MAVParamType.decode_float()` for more details and an example.
    """
    return MAVParamType(type).decode_float(value)


def encode_param_to_wire_representation(value, type: MAVParamType) -> float:
    """Encodes the given value as a given MAVLink type, ready to be transferred
    to the remote end encoded as a float.

    This is a quirk of the MAVLink parameter protocol where the official,
    over-the-wire type of each parameter is a float, but sometimes we want to
    transfer, say 32-bit integers. In this case, the 32-bit integer
    representation is _reinterpreted_ as a float, and the resulting float value
    is sent over the wire; the other side will then _reinterpret_ it again as
    a 32-bit integer.

    See `MAVParamType.as_float()` for more details and an example.
    """
    return MAVParamType(type).as_float(value)


def flockwave_severity_from_mavlink_severity(severity: int) -> Severity:
    """Returns the Flockwave log message severity level corresponding to the
    given MAVLink severity level.
    """
    if severity <= 0:
        return Severity.CRITICAL
    elif severity >= 8:
        return Severity.DEBUG
    else:
        return _mavlink_severity_to_flockwave_severity[severity]


def log_id_from_message(
    message: MAVLinkMessage, network_id: Optional[str] = None
) -> str:
    """Returns an identifier composed from the MAVLink system and component ID
    that is suitable for displaying in the logging output.
    """
    system_id, component_id = message.get_srcSystem(), message.get_srcComponent()
    if network_id:
        return f"{network_id}/{system_id}:{component_id}"
    else:
        return f"{system_id}:{component_id}"


def log_id_for_uav(uav: MAVLinkUAV) -> str:
    """Returns an identifier for a single UAV that is suitable for displaying in
    the logging output.

    Based on user feedback, we are not showing the network and system ID here,
    only the UAV ID.
    """
    return uav.id


def mavlink_nav_command_to_gps_coordinate(message: MAVLinkMessage) -> GPSCoordinate:
    """Creates a GPSCoordinate object from the parameters of a MAVLink
    `MAV_CMD_NAV_...` command typically used in mission descriptions.

    Parameters:
        message: the MAVLink message with fields named `x`, `y` and `z`. It is
            assumed (and not checked) that the message is a MAVLink command
            of type `MAV_CMD_NAV_...`.
    """
    if message.frame in (MAVFrame.GLOBAL, MAVFrame.GLOBAL_INT):
        return GPSCoordinate(lat=message.x / 1e7, lon=message.y / 1e7, amsl=message.z)
    elif message.frame in (
        MAVFrame.GLOBAL_RELATIVE_ALT,
        MAVFrame.GLOBAL_RELATIVE_ALT_INT,
    ):
        return GPSCoordinate(lat=message.x / 1e7, lon=message.y / 1e7, ahl=message.z)
    else:
        raise ValueError(f"unknown coordinate frame: {message.frame}")


def mavlink_version_number_to_semver(
    number: int, custom: Optional[list[int]] = None
) -> str:
    """Converts a version number found in the MAVLink `AUTOPILOT_VERSION` message
    to a string representation, in semantic version number format.

    Parameters:
        number: the numeric representation of the version number
        custom: the MAVLink representation of the "custom" component of the
            version number, if known; typically the first few bytes of a
            VCS hash
    """
    major = (number >> 24) & 0xFF
    minor = (number >> 16) & 0xFF
    patch = (number >> 8) & 0xFF
    prerelease = number & 0xFF

    version = [f"{major}.{minor}.{patch}"]

    # prerelease component is interpreted according to how ArduPilot uses it
    official = prerelease == 255
    if prerelease < 64:
        version.append(f"-dev.{prerelease}")
    elif prerelease < 128:
        version.append(f"-alpha.{prerelease - 64}")
    elif prerelease < 192:
        version.append(f"-beta.{prerelease - 128}")
    elif not official:
        version.append(f"-rc.{prerelease - 192}")

    if custom and not official:
        version.append(
            "+" + bytes(custom).rstrip(b"\x00").decode("utf-8", errors="ignore")
        )

    return "".join(version)


def python_log_level_from_mavlink_severity(severity: int) -> int:
    """Converts a MAVLink STATUSTEXT message severity (MAVSeverity) into a
    compatible Python log level.
    """
    if severity <= 0:
        return ERROR
    elif severity >= 8:
        return DEBUG
    else:
        return _mavlink_severity_to_python_log_level[severity]


class ChunkAssemblerRange(NamedTuple):
    """A single range returned from a ChunkAssembler_ when it proposes the
    next range to fetch.
    """

    offset: int
    """The offset of the range."""

    size: int
    """The size of the range."""

    @property
    def start(self) -> int:
        return self.offset

    @property
    def end(self) -> int:
        return self.offset + self.size


class ChunkAssembler:
    """Helper object to assemble a downloaded file from its chunks. This class
    is used by the log downloader at the moment and may be used with the MAVFTP
    downloader in the future if we implement bursty reads.
    """

    # NOTE: this class has a copy in `cmtool`. If you fix a bug here, consider
    # fixing it in the copy in `cmtool` as well.

    _num_flushed: int = 0
    """Number of bytes already flushed to the disk because all the chunks up
    to (and not including) this byte were already received successfully.
    """

    _num_pending: int = 0
    """Number of bytes in the pending queue."""

    _size: int
    """Size of the file being downloaded."""

    _pending: list[tuple[int, bytes]]
    """Pending chunks that were received but not flushed to the disk yet
    because there are gaps in front of them.
    """

    def __init__(self, size: int):
        """Constructor."""
        self._size = size
        self._pending = []
        self._num_flushed = 0
        self._num_pending = 0

    def add_chunk(self, offset: int, data: bytes) -> Optional[bytes]:
        """Adds a new chunk to the chunk assembler, starting at the given
        offset.

        Arguments:
            offset: the offset where the given chunk begins in the file
            data: the chunk itself

        Returns:
            an optional non-empty byte string that may be flushed to the
            disk locally
        """
        if offset < self._num_flushed:
            data = data[self._num_flushed - offset :]
            offset = self._num_flushed

        if not data:
            return

        if offset == self._num_flushed:
            # This is the next chunk so we can store it straight away
            self._num_flushed += len(data)
            if not self._pending or self._pending[0][0] > self._num_flushed:
                return data

            to_return: list[bytes] = [data]
            while self._pending:
                top = self._pending[0]
                if top[0] > self._num_flushed:
                    break

                if top[0] < self._num_flushed:
                    data = top[1][self._num_flushed - top[0] :]
                else:
                    data = top[1]

                self._num_flushed += len(data)
                to_return.append(data)

                item = heappop(self._pending)
                self._num_pending -= len(item[1])

            return b"".join(to_return)
        else:
            # There is a gap so store this chunk in self._pending
            heappush(self._pending, (offset, data))
            self._num_pending += len(data)

    def get_next_range(self, max_size: int = -1) -> ChunkAssemblerRange:
        """Returns the offset and size of the next range to fetch from the
        file with a bursted read.

        Args:
            max_size: the maximum size of the next range to return; negative
                if there is no upper limit
        """
        end = self._pending[0][0] if self._pending else self._size
        size = end - self._num_flushed
        if max_size >= 0:
            size = min(size, max_size)
        return ChunkAssemblerRange(self._num_flushed, size)

    def shorten_to(self, size: int) -> None:
        """Shortens the expected length of the file to the given size."""
        if self._size < size:
            raise RuntimeError(
                f"Cannot shorten a file with expected length {self._size} "
                f"to {size} bytes"
            )
        elif self._size == size:
            return

        self._size = size

        new_pending: list[tuple[int, bytes]] = []
        for offset, data in self._pending:
            end = offset + len(data)
            if end > self._size:
                data = data[: (self._size - offset)]
            if data:
                new_pending.append((offset, data))

        self._pending = new_pending
        self._num_pending = sum(len(data) for _, data in self._pending)

    @property
    def done(self) -> bool:
        """Returns whether there are no more chunks to fetch."""
        return self._num_flushed >= self._size

    def done_with(self, range: ChunkAssemblerRange) -> bool:
        """Returns whether there are no more chunks to fetch in the given range."""
        return self._num_flushed >= range.end

    @property
    def num_flushed(self) -> int:
        """Returns the total number of bytes already flushed to disk."""
        return self._num_flushed

    @property
    def num_flushed_and_queued(self) -> int:
        """Returns the total number of bytes already flushed to disk or
        sitting in the queue.
        """
        return self._num_flushed + self._num_pending

    @property
    def percentage(self) -> float:
        """Returns the percentage of the expected data that has already been
        flushed to disk or sitting in the queue, rounded to one decimal digit.
        """
        return round(100.0 * (self._num_flushed + self._num_pending) / self._size, 1)
