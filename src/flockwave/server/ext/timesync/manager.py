"""Core implementation of the `timesync` extension."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from math import inf
from time import monotonic, time
from typing import ClassVar

from blinker import Signal
from trio import BrokenResourceError
from trio_util import periodic

from .constants import (
    DEFAULT_OFFSET_LOG_THRESHOLD,
    DEFAULT_SOURCE_EXPIRY_THRESHOLD,
    DEFAULT_SYNC_THRESHOLD,
    MAX_REFRESH_PERIOD,
    MIN_REFRESH_PERIOD,
)
from .model import TimeSourceRecord, TimeSyncSnapshot
from .types import LoggerLike, TimeSyncState

__all__ = ("TimeSyncManager", "TimeSource")


class TimeSyncManager:
    """Tracks time sources and determines the synchronization state."""

    sync_status_changed: ClassVar[Signal] = Signal()
    """Signal sent whenever the synchronization state changes.

    The signal handler receives two keyword arguments: `current` containing the current
    synchronization status and `previous` contianing the previous synchronization
    status.
    """

    sync_threshold: float
    """Maximum absolute offset, in seconds, for the local clock to be considered synchronized."""

    source_expiry_threshold: float
    """Maximum age of a successful report, in seconds, before it is ignored."""

    offset_log_threshold: float
    """Minimum offset change, in seconds, that triggers a new log message."""

    _current_time: Callable[[], float]
    """Callable returning the current local server time as a UNIX timestamp.

    The returned value is the number of seconds elapsed since the Unix epoch in
    UTC according to the local system clock.
    """

    _current_monotonic_time: Callable[[], float]
    """Callable returning a monotonic timestamp for elapsed-time measurements.

    The returned value must come from a monotonic clock such as
    ``time.monotonic()`` and must never be interpreted as a UNIX timestamp.
    """

    _log: LoggerLike | None
    """Logger used for synchronization status messages, if logging is enabled."""

    _sources: dict[str, TimeSourceRecord]
    """Mapping from time source identifiers to their tracked internal state."""

    _snapshot: TimeSyncSnapshot
    """Most recently computed synchronization status snapshot."""

    _last_logged_state: TimeSyncState | None
    """Synchronization state that was included in the last emitted log message."""

    _last_logged_offset: float | None
    """Offset value that was included in the last emitted log message, in seconds."""

    def __init__(
        self,
        *,
        sync_threshold: float = DEFAULT_SYNC_THRESHOLD,
        source_expiry_threshold: float = DEFAULT_SOURCE_EXPIRY_THRESHOLD,
        offset_log_threshold: float = DEFAULT_OFFSET_LOG_THRESHOLD,
        current_time: Callable[[], float] = time,
        current_monotonic_time: Callable[[], float] = monotonic,
        log: LoggerLike | None = None,
    ):
        """Constructor.

        Args:
            sync_threshold: maximum absolute offset, in seconds, for the local
                clock to be considered synchronized
            source_expiry_threshold: maximum age of a successful report, in
                seconds, before it is ignored
            offset_log_threshold: minimum change in the selected offset, in
                seconds, that triggers a new log message while the state stays
                the same
            current_time: callable returning the current local UNIX timestamp
            current_monotonic: callable returning the current monotonic time
            log: logger used for state-change messages
        """
        self._current_time = current_time
        self._current_monotonic_time = current_monotonic_time
        self._sources = {}
        self._snapshot = TimeSyncSnapshot()
        self._last_logged_state = None
        self._last_logged_offset = None
        self._log = None

        self.configure(
            sync_threshold=sync_threshold,
            source_expiry_threshold=source_expiry_threshold,
            offset_log_threshold=offset_log_threshold,
            log=log,
        )

    def configure(
        self,
        *,
        sync_threshold: float = DEFAULT_SYNC_THRESHOLD,
        source_expiry_threshold: float = DEFAULT_SOURCE_EXPIRY_THRESHOLD,
        offset_log_threshold: float = DEFAULT_OFFSET_LOG_THRESHOLD,
        log: LoggerLike | None = None,
    ) -> None:
        """Updates the configuration of the manager in-place."""
        if sync_threshold < 0:
            raise ValueError("sync threshold must be non-negative")
        if source_expiry_threshold <= 0:
            raise ValueError("source expiry threshold must be positive")
        if offset_log_threshold < 0:
            raise ValueError("offset log threshold must be non-negative")

        self.sync_threshold = float(sync_threshold)
        self.source_expiry_threshold = float(source_expiry_threshold)
        self.offset_log_threshold = float(offset_log_threshold)
        self._log = log
        self.refresh_status()

    async def run(self) -> None:
        """Refreshes the status periodically so source expiry is detected."""
        refresh_period = max(
            MIN_REFRESH_PERIOD,
            min(self.source_expiry_threshold / 10, MAX_REFRESH_PERIOD),
        )
        async for _ in periodic(refresh_period):
            self.refresh_status()

    def get_priority_of_time_source(self, id: str) -> int:
        """Returns the priority of a registered time source."""
        return self._get_source(id).priority

    def get_status(self) -> TimeSyncSnapshot:
        """Returns the current synchronization status snapshot."""
        self.refresh_status()
        return self._snapshot

    def refresh_status(self) -> TimeSyncSnapshot:
        """Recomputes the synchronization state from the current source state."""
        previous = self._snapshot
        current = self._create_snapshot()
        self._snapshot = current

        if current != previous:
            self._maybe_log_status_change(current, previous)
            self.sync_status_changed.send(self, current=current, previous=previous)

        return current

    def register_time_source(self, id: str, *, priority: int = 0) -> TimeSource:
        """Registers a new time source and returns a handle for reporting."""
        if id in self._sources:
            raise KeyError(f"Time source is already registered: {id}")

        self._sources[id] = TimeSourceRecord(id=id, priority=int(priority))
        self.refresh_status()

        return TimeSource(self, id)

    def submit_error(self, id: str, error: str | Exception) -> None:
        """Marks the given time source as temporarily unavailable."""
        source = self._get_source(id)
        now_monotonic = self._current_monotonic_time()

        source.last_event_at_monotonic = now_monotonic
        source.last_error = self._format_error(error)
        self.refresh_status()

    def submit_offset(
        self, id: str, offset: float, *, jitter: float | None = None
    ) -> None:
        """Submits a wall-clock offset from the given time source.

        Args:
            id: identifier of the time source
            offset: difference between wall clock time and local server time, in
                seconds, defined as ``wall_clock_time - local_server_time``, according
                to the time source
            jitter: optional jitter estimate in seconds
        """
        self._update_source_from_measurement(
            id,
            offset=float(offset),
            jitter=jitter,
        )

    def submit_timestamp(
        self, id: str, unix_timestamp: float, *, jitter: float | None = None
    ) -> None:
        """Submits a wall-clock UNIX timestamp from the given time source.

        Args:
            id: identifier of the time source
            unix_timestamp: true wall-clock UNIX timestamp in seconds since the Unix
                epoch in UTC according to the time source
            jitter: optional jitter estimate in seconds
        """
        self.submit_offset(
            id, float(unix_timestamp) - self._current_time(), jitter=jitter
        )

    def unregister_time_source(self, id: str) -> None:
        """Unregisters a previously registered time source."""
        self._sources.pop(id, None)

        try:
            self.refresh_status()
        except BrokenResourceError:
            # This may happen if the server is shutting down because refresh_status()
            # might send a time sync status change signal, which may in turn prompt the
            # server to send a TIMESYNC-STATUS notification to clients.
            pass

    @contextmanager
    def use_time_source(
        self, id: str, *, priority: int = 0
    ) -> Generator[TimeSource, None, None]:
        """Registers a time source for the duration of a context."""
        source = self.register_time_source(id, priority=priority)
        try:
            yield source
        finally:
            self.unregister_time_source(id)

    def _create_snapshot(self) -> TimeSyncSnapshot:
        """Constructs a fresh snapshot from the currently tracked sources."""
        now_monotonic = self._current_monotonic_time()

        records = list(self._sources.values())
        reported_records = [record for record in records if record.has_report]
        active_records = [
            record
            for record in reported_records
            if record.is_active(now_monotonic, self.source_expiry_threshold)
        ]
        error_records = [record for record in reported_records if record.is_error]

        selected = (
            max(
                active_records,
                key=lambda record: (
                    record.priority,
                    record.last_success_at_monotonic or -inf,
                    record.id,
                ),
            )
            if active_records
            else None
        )

        if selected is not None:
            state: TimeSyncState = (
                "sync"
                if abs(selected.last_reported_offset or 0.0) <= self.sync_threshold
                else "unsync"
            )
            return TimeSyncSnapshot(
                state=state,
                selected_source=selected.id,
                offset=selected.last_reported_offset,
                jitter=selected.last_reported_jitter,
            )

        state = (
            "error"
            if reported_records and len(error_records) == len(reported_records)
            else "unknown"
        )
        return TimeSyncSnapshot(state=state)

    def _get_source(self, id: str) -> TimeSourceRecord:
        """Returns the internal record for a registered time source."""
        try:
            return self._sources[id]
        except KeyError as ex:
            raise KeyError(f"No such time source: {id}") from ex

    def _maybe_log_status_change(
        self, current: TimeSyncSnapshot, previous: TimeSyncSnapshot
    ) -> None:
        """Emits a log message if the externally visible status changed enough."""
        should_log = False

        if self._last_logged_state is None:
            # If we have no previous logged state, we show the new status if it is
            # not "unknown" (i.e. if we have a valid initial status).
            should_log = current.state != "unknown"
        elif current.state != self._last_logged_state:
            # Always log when the sync / unsync / error state changes
            should_log = True
        elif current.offset is not None and self._last_logged_offset is None:
            # Always log when we have the first offset
            should_log = True
        elif (
            current.offset is not None
            and self._last_logged_offset is not None
            and abs(current.offset - self._last_logged_offset)
            >= self.offset_log_threshold
        ):
            # Always log when the offset changes significantly
            should_log = True

        self._last_logged_state = current.state
        self._last_logged_offset = current.offset

        if not should_log or not self._log:
            return

        match current.state:
            case "sync":
                self._log.info(
                    "Server clock is synchronized to world clock",
                    extra={"semantics": "success"},
                )

            case "unsync":
                offset = current.offset or 0.0
                if offset < 0:
                    self._log.warning(
                        f"Server clock is ahead of wall clock time by {-offset:.3}s"
                    )
                else:
                    self._log.warning(
                        f"Server clock is behind wall clock time by {offset:.3}s"
                    )

            case "error":
                self._log.warning(
                    "Cannot determine server clock synchronization state: "
                    "all reporting time sources indicated errors"
                )

            case _:
                if self._sources:
                    self._log.info(
                        "Cannot determine server clock synchronization state: "
                        "no recent timestamp is available"
                    )

    def _update_source_from_measurement(
        self,
        id: str,
        *,
        offset: float,
        jitter: float | None,
    ) -> None:
        """Stores a successful report from a time source.

        Args:
            id: identifier of the time source
            unix_timestamp: wall-clock UNIX timestamp, in seconds since the Unix
                epoch in UTC
            offset: wall-clock offset in seconds, defined as
                ``wall_clock_time - local_server_time``
            jitter: optional jitter estimate in seconds
        """
        if jitter is not None and jitter < 0:
            raise ValueError("jitter must be non-negative")

        source = self._get_source(id)
        now_monotonic = self._current_monotonic_time()

        source.last_event_at_monotonic = now_monotonic
        source.last_success_at_monotonic = now_monotonic
        source.last_reported_jitter = (
            round(float(jitter), 3) if jitter is not None else None
        )
        source.last_reported_offset = round(float(offset), 3)
        source.last_error = None
        self.refresh_status()

    @staticmethod
    def _format_error(error: str | Exception) -> str:
        """Formats an error object into a concise human-readable message."""
        if isinstance(error, Exception):
            message = str(error)
            return message or error.__class__.__name__

        return str(error)


class TimeSource:
    """Handle returned to extensions that want to report wall clock time."""

    _manager: TimeSyncManager
    """Manager instance that owns this source handle."""

    _id: str
    """Identifier of the associated time source."""

    def __init__(self, manager: TimeSyncManager, id: str):
        """Constructor.

        Args:
            manager: manager that owns this source handle
            id: identifier of the associated time source
        """
        self._manager = manager
        self._id = id

    @property
    def id(self) -> str:
        """Identifier of the associated time source."""
        return self._id

    @property
    def priority(self) -> int:
        """Priority of the associated time source."""
        return self._manager.get_priority_of_time_source(self._id)

    def submit_error(self, error: str | Exception) -> None:
        """Reports that the time source is temporarily in an error state."""
        self._manager.submit_error(self._id, error)

    def submit_offset(self, offset: float, *, jitter: float | None = None) -> None:
        """Reports a wall-clock offset from the associated time source.

        Args:
            offset: difference between wall clock time and local server time, in
                seconds, defined as ``wall_clock_time - local_server_time``
            jitter: optional jitter estimate in seconds
        """
        self._manager.submit_offset(self._id, offset, jitter=jitter)

    def submit_timestamp(
        self, unix_timestamp: float, *, jitter: float | None = None
    ) -> None:
        """Reports a wall-clock UNIX timestamp from the associated source.

        Args:
            unix_timestamp: wall-clock UNIX timestamp in seconds since the Unix
                epoch in UTC
            jitter: optional jitter estimate in seconds
        """
        self._manager.submit_timestamp(self._id, unix_timestamp, jitter=jitter)
