"""Implementation of downloading logs via a MAVLink connection."""

from contextlib import aclosing
from functools import partial
from trio import (
    MemoryReceiveChannel,
    MemorySendChannel,
    WouldBlock,
    current_time,
    fail_after,
    move_on_after,
    open_memory_channel,
    TooSlowError,
)
from typing import Callable, Optional

from flockwave.concurrency import Future
from flockwave.logger import Logger
from flockwave.server.model.commands import Progress, ProgressEvents
from flockwave.server.model.log import FlightLog, FlightLogKind, FlightLogMetadata

from .types import (
    MAVLinkMessage,
    MAVLinkMessageSpecification,
    spec,
)
from .utils import ChunkAssembler

__all__ = ("MAVLinkLogDownloader",)


def create_log_metadata_from_mavlink_message(
    message: MAVLinkMessage,
) -> FlightLogMetadata:
    """Constructs a log metadata entry from a MAVLink ``LOG_ENTRY`` message."""
    # TODO(ntamas): support PX4 logs!
    return FlightLogMetadata.create(
        id=message.id,
        kind=FlightLogKind.ARDUPILOT,
        size=message.size,
        timestamp=message.time_utc or None,
    )


class MAVLinkLogDownloader:
    """Object that can be used to download logs from a MAVLink drone via a
    MAVLink connection.
    """

    _sender: Callable
    """A function that can be called to send a MAVLink message over the
    connection associated to this MAVFTP object.

    It must be API-compatible with the `send_packet()` method of the MAVLinkUAV_
    object.
    """

    _log: Optional[Logger]
    """Logger that the manager object can use to log messages."""

    _log_being_downloaded: Optional[int] = None
    """ID of the log that is currently being downloaded."""

    _log_listing_future: Optional[Future[list[FlightLogMetadata]]] = None
    """Future that resolves when the log listing operation completes."""

    _message_channel: Optional[MemorySendChannel[MAVLinkMessage]] = None
    """Trio channel on which we feed the current log listing operation with new
    MAVLink messages to process.
    """

    @classmethod
    def for_uav(cls, uav):
        """Constructs a MAVFTP connection object to the given UAV."""
        sender = partial(uav.driver.send_packet, target=uav)
        log = uav.driver.log
        return cls(sender, log=log)

    def __init__(self, sender: Callable, log: Optional[Logger] = None):
        self._sender = sender
        self._log = log
        self._retries = 5

    async def get_log(self, log_id: int) -> ProgressEvents[Optional[FlightLog]]:
        """Retrieves a single log with the given ID from the drone."""
        if self._log_being_downloaded is not None:
            raise RuntimeError("Another log download is in progress")

        if self._log_listing_future is not None:
            raise RuntimeError(
                "Cannot download log: log listing is currently being retrieved"
            )

        self._log_being_downloaded = log_id
        self._message_channel, rx = open_memory_channel(128)
        try:
            async with aclosing(self._get_log_inner(log_id, rx)) as it:
                async for item in it:
                    yield item
        finally:
            self._log_being_downloaded = None
            self._message_channel = None

    async def get_log_list(self) -> list[FlightLogMetadata]:
        """Retrieves the list of logs from the drone.

        A single drone supports a single log listing operation only. When you
        call this function while another log listing operation is in progress,
        you will get an awaitable that resolves when the _other_ operation that
        is already in progress finishes.
        """
        if self._log_being_downloaded is not None:
            raise RuntimeError("Another log download is in progress")

        if self._log_listing_future is not None:
            return await self._log_listing_future.wait()

        self._log_listing_future = future = Future()
        self._message_channel, rx = open_memory_channel(128)
        try:
            await self._log_listing_future.call(self._get_log_list_inner, rx)
        finally:
            self._log_listing_future = None
            self._message_channel = None

        return future.result()

    def handle_message_log_data(self, message: MAVLinkMessage):
        if self._message_channel:
            try:
                self._message_channel.send_nowait(message)
            except WouldBlock:
                if self._log:
                    self._log.warning("Incoming log data message dropped, queue full")

    def handle_message_log_entry(self, message: MAVLinkMessage):
        if self._message_channel:
            try:
                self._message_channel.send_nowait(message)
            except WouldBlock:
                if self._log:
                    self._log.warning("Incoming log entry message dropped, queue full")

    async def _get_log_inner(
        self, log_id: int, rx: MemoryReceiveChannel[MAVLinkMessage]
    ) -> ProgressEvents[Optional[FlightLog]]:
        last_progress_at = current_time()

        # We are requesting at most 512 LOG_DATA messages at once to let the
        # wifi module also take care of other things while doing the download.
        # Requesting the entire log at once has caused timeout problems with
        # mavesp8266. The strategy adopted here is identical to what
        # QGroundControl is doing
        MAX_CHUNK_SIZE = 512 * 90

        try:
            # Get the size of the log first and create a chunk assembler
            metadata = await self._get_single_log_metadata(log_id)
            if not metadata:
                yield None
                return

            if metadata.size is None:
                raise RuntimeError("unknown log size")

            chunks = ChunkAssembler(metadata.size)
            log_data: list[bytes] = []
            while not chunks.done:
                next_range = chunks.get_next_range(max_size=MAX_CHUNK_SIZE)
                response: Optional[MAVLinkMessage] = await self._send_and_wait(
                    spec.log_request_data(
                        id=log_id, ofs=next_range.offset, count=next_range.size
                    ),
                    spec.log_data(),
                )

                # Process the response, and start processing any other LOG_DATA messages
                # that we receive via the channel
                while response is not None:
                    if response.get_type() == "LOG_DATA":
                        if response.id != log_id:
                            # ignore
                            pass

                        to_flush = chunks.add_chunk(
                            response.ofs, bytes(response.data[: response.count])
                        )
                        if to_flush:
                            log_data.append(to_flush)

                    response = None
                    if not chunks.done_with(next_range):
                        with move_on_after(3):
                            response = await rx.receive()

                    now = current_time()
                    if now - last_progress_at > 0.1:
                        yield Progress(
                            percentage=round(chunks.percentage),
                            message="Downloading log...",
                        )
                        last_progress_at = current_time()

        finally:
            await self._sender(spec.log_request_end())

        yield FlightLog.create_from_metadata(metadata, body=b"".join(log_data))

    async def _get_log_list_inner(
        self, rx: MemoryReceiveChannel[MAVLinkMessage]
    ) -> list[FlightLogMetadata]:
        # Number of logs to download; ``None`` if we do not know it yet
        num_logs: Optional[int] = None
        logs: dict[int, FlightLogMetadata] = {}

        try:
            while num_logs is None or len(logs) < num_logs:
                start_index = 0
                while start_index in logs:
                    start_index += 1

                response = await self._send_and_wait(
                    spec.log_request_list(start=start_index, end=0xFFFF),
                    spec.log_entry(),
                )

                # Process the response, and start processing any other LOG_ENTRY
                # messages that we receive via the channel
                num_logs = response.num_logs
                assert num_logs is not None
                if num_logs > 0:
                    logs[response.id] = create_log_metadata_from_mavlink_message(
                        response
                    )

                while len(logs) < num_logs:
                    # Wait for more LOG_ENTRY messages
                    response = None
                    with move_on_after(3):
                        response = await rx.receive()
                    if response is not None and response.get_type() == "LOG_ENTRY":
                        logs[response.id] = create_log_metadata_from_mavlink_message(
                            response
                        )

        finally:
            await self._sender(spec.log_request_end())

        log_list = [logs[index] for index in sorted(logs.keys())]
        return log_list

    async def _get_single_log_metadata(
        self, log_id: int
    ) -> Optional[FlightLogMetadata]:
        response = await self._send_and_wait(
            spec.log_request_list(start=log_id, end=log_id),
            spec.log_entry(),
        )

        num_logs = response.num_logs
        assert num_logs is not None
        if num_logs > 0:
            return create_log_metadata_from_mavlink_message(response)
        else:
            return None

    async def _send_and_wait(
        self,
        message: MAVLinkMessageSpecification,
        expected_reply: MAVLinkMessageSpecification,
        *,
        timeout: float = 1.5,
        retries: int = 5,
    ) -> MAVLinkMessage:
        """Sends a message according to the given MAVLink message specification
        to the drone and waits for an expected reply, re-sending the message
        as needed a given number of times before timing out.

        Parameters:
            mission_type: type of the mission we are dealing with
            message: specification of the message to send
            expected_reply: message matcher that matches messages that we expect
                from the connection as a reply to the original message
            timeout: maximum number of seconds to wait before attempting to
                re-send the message
            retries: maximum number of retries before giving up

        Returns:
            the MAVLink message sent by the UAV in response

        Raises:
            TooSlowError: if the UAV failed to respond in time
        """
        while True:
            try:
                with fail_after(timeout):
                    return await self._sender(message, wait_for_response=expected_reply)

            except TooSlowError:
                if retries > 0:
                    retries -= 1
                    continue
                else:
                    raise TooSlowError("MAVLink mission operation timed out") from None
