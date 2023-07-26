"""Extension that provides other extensions with an append-only audit log
database where other extensions can register events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from logging import Logger
from pathlib import Path
from textwrap import dedent
from time import time
from trio import move_on_after, sleep, to_thread
from trio.lowlevel import ParkingLot
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Optional,
    Sequence,
    Tuple,
    TYPE_CHECKING,
    Union,
)

from .base import Extension

if TYPE_CHECKING:
    from sqlite3 import Connection


MAX_BUFFER_SIZE = 1024
"""Maximum number of entries that the extension may buffer without flushing them
to the underlying data store before it starts to drop entries.
"""


@dataclass(frozen=True)
class Entry:
    timestamp: float = field(default_factory=time)
    """The timestamp of the entry."""

    component: str = ""
    """The component that created the entry."""

    type: str = ""
    """The type of the entry."""

    data: bytes = b""
    """Additional data blob attached to the entry."""

    def to_tuple(self) -> Tuple[float, str, str, bytes]:
        return (self.timestamp, self.component, self.type, self.data)


class Storage(ABC):
    """Interface specification for storage backends of the audit log."""

    @abstractmethod
    async def put(self, entries: Sequence[Entry]) -> None:
        """Writes the given entries into the storage backend."""
        raise NotImplementedError

    @asynccontextmanager
    async def use(self, log: Logger) -> AsyncIterator[None]:
        """Establishes a context within which the storage backend can be used."""
        yield


class NullStorage(Storage):
    """Dummy storage backend that does not store anything."""

    async def put(self, entries: Sequence[Entry]) -> None:
        pass


class InMemoryStorage(Storage):
    """In-memory storage backend for the audit log.

    Contents of this storage backend are lost when the server is restarted.
    """

    _entries: list[Entry]

    def __init__(self) -> None:
        self._entries = []

    async def put(self, entries: Sequence[Entry]) -> None:
        print(f"Flushing {len(entries)} entries to in-memory storage")
        self._entries.extend(entries)

    @asynccontextmanager
    async def use(self, log: Logger):
        log.warn(
            "Using in-memory audit log storage. Log entries will not be persisted."
        )
        yield


class DbStorage(Storage):
    """Storage backend backed by an on-disk SQLite database."""

    _conn: Optional[Connection] = None
    """Connection to the underlying SQLite database."""

    _path: Path
    """Path to the SQLite database that the backend writes to."""

    def __init__(self, path: Union[str, Path]):
        self._path = Path(path)

    async def put(self, entries: Sequence[Entry]) -> None:
        await to_thread.run_sync(self._put_sync, entries)

    def _put_sync(self, entries: Sequence[Entry]) -> None:
        if self._conn:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO entries (timestamp, component, type, data) "
                    "VALUES (?, ?, ?, ?)",
                    [entry.to_tuple() for entry in entries],
                )

    @asynccontextmanager
    async def use(self, log: Logger):
        from sqlite3 import Connection

        if self._conn is not None:
            raise RuntimeError("storage backend is already in use")

        log.info(f"Saving audit log to {str(self._path)!r}")

        self._conn = Connection(self._path, check_same_thread=False)
        await to_thread.run_sync(self._prepare_schema)
        try:
            yield
        finally:
            await to_thread.run_sync(self._conn.close)

    def _prepare_schema(self) -> None:
        """Prepares the schema of the audit log database."""
        assert self._conn is not None
        with self._conn:
            # No indexing as we assume frequent inserts and only occasional
            # SELECT() queries for reporting purposes
            self._conn.executescript(
                dedent(
                    """\
                    CREATE TABLE IF NOT EXISTS meta (
                        key varchar(255) PRIMARY KEY,
                        value text
                    );

                    INSERT OR IGNORE INTO meta (key, value)
                    VALUES ("version", "1");

                    CREATE TABLE IF NOT EXISTS entries (
                        id INTEGER PRIMARY KEY ASC,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        component TEXT,
                        type TEXT,
                        data BLOB
                    );
                    """
                )
            )


class AuditLogExtension(Extension):
    """Extension that provides other extensions with an append-only audit log
    database where other extensions can register events.

    The audit log may then be post-processed later using external scripts to
    produce basic reports.

    As an end user, you typically won't need to enable this extension directly.
    Other extensions relying on the audit log will declare it as a dependency so
    it gets enabled automatically if needed.
    """

    _entries: deque[Entry]
    """The entries waiting to be flushed to the storage backend."""

    _parking_lot: ParkingLot

    _storage: Storage
    """Storage backend used by the extension."""

    def __init__(self):
        super().__init__()
        self._entries = deque(maxlen=MAX_BUFFER_SIZE)
        self._parking_lot = ParkingLot()
        self._storage = NullStorage()

    def append(self, component: str, type: str, data: Union[str, bytes] = b"") -> None:
        """Appends a new entry to the audit log.

        The appended entry may not written to the log immediately for sake of
        efficiency. Call `flush()` if you want to ensure that the entry is
        flushed to the storage backend.
        """
        if isinstance(data, str):
            data = data.encode("utf-8")

        self._entries.append(Entry(time(), component, type, data))
        self._parking_lot.unpark()

    def configure(self, configuration: dict[str, Any]) -> None:
        db_path = self.get_data_dir() / "log.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage = DbStorage(db_path)

    def exports(self) -> dict[str, Any]:
        return {"append": self.append, "flush": self.flush}

    async def flush(self) -> None:
        """Flushes all pending entries to the audit log."""
        if self._entries:
            entries = list(self._entries)
            self._entries.clear()
            await self._storage.put(entries)

    def get_logger(self, component: str) -> Callable[[str, Union[str, bytes]], None]:
        """Returns a logger function that can be called with a message type and
        an attached data object and that appends a new entry to the audit log
        with the given component name.
        """
        return partial(self.append, component)

    async def run(self):
        audit_logger = self.get_logger("audit_log")

        self._entries.clear()
        audit_logger("start", "")

        async with self._storage.use(self.log):
            try:
                while True:
                    while self._entries:
                        await self.flush()
                        await sleep(0.5)

                    # At this point there are no more entries waiting in the queue,
                    # so suspend ourselves and wait until some more entries appear
                    # in the queue
                    await self._parking_lot.park()
            finally:
                # Make sure that self.flush() still goes through even though
                # the nursery is cancelled
                with move_on_after(3) as cleanup_scope:
                    cleanup_scope.shield = True
                    audit_logger("stop", "")
                    await self.flush()
                    self.log.info("Audit log closed")


construct = AuditLogExtension
description = "Audit log provider for other extensions"
schema = {}
