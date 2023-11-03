"""Extension that provides other extensions with an append-only audit log
database where other extensions can register events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from logging import Logger
from math import inf
from pathlib import Path
from textwrap import dedent
from time import time
from trio import move_on_after, sleep, to_thread
from trio.lowlevel import ParkingLot
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Iterable,
    Optional,
    Sequence,
    TYPE_CHECKING,
    TypeVar,
    Union,
)

from flockwave.server.utils import constant

from .base import Extension

if TYPE_CHECKING:
    from sqlite3 import Connection


MAX_BUFFER_SIZE = 1024
"""Maximum number of entries that the extension may buffer without flushing them
to the underlying data store before it starts to drop entries.
"""


def days_ago(age: float) -> float:
    """Helper function that returns the timestamp the given number of dayds
    before the current time.
    """
    return (datetime.now() - timedelta(days=age)).timestamp()


T = TypeVar("T")


def to_timestamp(
    value: Union[float, datetime, None], *, default: T = None
) -> Union[float, T]:
    if value is None:
        return default
    elif isinstance(value, datetime):
        return value.timestamp()
    else:
        return value


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

    def to_tuple(self) -> tuple[float, str, str, bytes]:
        return (self.timestamp, self.component, self.type, self.data)


class Storage(ABC):
    """Interface specification for storage backends of the audit log."""

    @abstractmethod
    async def prune(self, threshold: float) -> Optional[int]:
        """Removes all the entries from the storage backend whose timestamp
        is smaller than the given threshold.

        Returns:
            the number of entries purged; ``None`` if unknown
        """
        raise NotImplementedError

    @abstractmethod
    async def put(self, entries: Sequence[Entry]) -> None:
        """Writes the given entries into the storage backend."""
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        component: Union[str, Iterable[str], None] = None,
        *,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        raise NotImplementedError

    @asynccontextmanager
    async def use(self, log: Logger) -> AsyncIterator[None]:
        """Establishes a context within which the storage backend can be used."""
        yield


class NullStorage(Storage):
    """Dummy storage backend that does not store anything."""

    async def prune(self, threshold: float) -> None:
        pass

    async def put(self, entries: Sequence[Entry]) -> None:
        pass

    async def query(
        self,
        component: Union[str, Iterable[str], None] = None,
        *,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        return ()


class InMemoryStorage(Storage):
    """In-memory storage backend for the audit log.

    Contents of this storage backend are lost when the server is restarted.
    """

    _entries: list[Entry]

    def __init__(self) -> None:
        self._entries = []

    async def prune(self, threshold: float) -> int:
        num_entries = len(self._entries)
        self._entries = [
            entry for entry in self._entries if entry.timestamp >= threshold
        ]
        return num_entries - len(self._entries)

    async def put(self, entries: Sequence[Entry]) -> None:
        self._entries.extend(entries)

    async def query(
        self,
        component: Union[str, Iterable[str], None] = None,
        *,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        min_timestamp = to_timestamp(min_date, default=-inf)
        max_timestamp = to_timestamp(max_date, default=inf)

        if component is None:
            component_matcher = constant(True)
        else:
            if isinstance(component, str):
                component = (component,)
            else:
                component = tuple(component)
            component_matcher = component.__contains__

        return [
            entry
            for entry in self._entries
            if (
                entry.timestamp >= min_timestamp
                and entry.timestamp < max_timestamp
                and component_matcher(entry.component)
            )
        ]

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

    async def prune(self, threshold: float) -> int:
        return await to_thread.run_sync(self._prune_sync, threshold)

    async def put(self, entries: Sequence[Entry]) -> None:
        await to_thread.run_sync(self._put_sync, entries)

    async def query(
        self,
        component: Union[str, Iterable[str], None] = None,
        *,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        return await to_thread.run_sync(self._query_sync, component, min_date, max_date)

    def _prune_sync(self, threshold: float) -> int:
        if self._conn:
            with self._conn:
                with closing(self._conn.cursor()) as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM entries WHERE timestamp < ?", (threshold,)
                    )
                    (count,) = cur.fetchone()
                if count > 0:
                    self._conn.execute(
                        "DELETE FROM entries WHERE timestamp < ?", (threshold,)
                    )
            return count
        else:
            return 0

    def _put_sync(self, entries: Sequence[Entry]) -> None:
        if self._conn:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO entries (timestamp, component, type, data) "
                    "VALUES (?, ?, ?, ?)",
                    [entry.to_tuple() for entry in entries],
                )

    def _query_sync(
        self,
        component: Union[str, Iterable[str], None] = None,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        result: list[Entry] = []

        if self._conn:
            query = "SELECT timestamp, component, type, data FROM entries"
            conditions: list[str] = []
            args: list[Any] = []

            if component is not None:
                components = (
                    (component,) if isinstance(component, str) else list(component)
                )

                if len(components) == 0:
                    condition = "FALSE"
                elif len(components) == 1:
                    condition = "component = ?"
                else:
                    qmarks = ", ".join("?" for _ in components)
                    condition = f"component IN ({qmarks})"

                conditions.append(condition)
                args.extend(components)

            min_timestamp = to_timestamp(min_date)
            max_timestamp = to_timestamp(max_date)

            if min_timestamp is not None:
                conditions.append("timestamp >= ?")
                args.append(min_timestamp)

            if max_timestamp is not None:
                conditions.append("timestamp < ?")
                args.append(max_timestamp)

            if conditions:
                conditions_joined = " AND ".join(conditions)
                query = f"{query} WHERE {conditions_joined}"

            with self._conn:
                with closing(self._conn.cursor()) as cur:
                    result.extend(Entry(*row) for row in cur.execute(query, args))

        return result

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

    max_age_days: float
    """Maximum age of entries in the audit log, in days."""

    _entries: deque[Entry]
    """The entries waiting to be flushed to the storage backend."""

    _storage: Storage
    """Storage backend used by the extension."""

    _parking_lot: ParkingLot

    def __init__(self):
        super().__init__()
        self._entries = deque(maxlen=MAX_BUFFER_SIZE)
        self._parking_lot = ParkingLot()
        self._storage = NullStorage()
        self.max_age = 0

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

        maybe_max_age = configuration.get("max_age")
        if maybe_max_age is None:
            self.max_age_days = 365  # default
        elif (
            maybe_max_age
            and isinstance(maybe_max_age, (int, float))
            and maybe_max_age > 0
        ):
            self.max_age_days = float(maybe_max_age)
        else:
            self.max_age_days = 0  # unlimited

        self._storage = DbStorage(db_path)

    def exports(self) -> dict[str, Any]:
        return {"append": self.append, "flush": self.flush, "query": self.query}

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

    async def query(
        self,
        component: Union[str, Iterable[str], None] = None,
        *,
        min_date: Union[datetime, float, None] = None,
        max_date: Union[datetime, float, None] = None,
    ) -> Iterable[Entry]:
        """Retrieves all entries from the audit log that match the given search
        criteria.

        Args:
            component: the component or components that the log entries must
                belong to
            min_date: the earliest date of the matched log entries, inclusive
            max_date: the latest date of the matched log entries, _exclusive_
        """
        return await self._storage.query(
            component, min_date=min_date, max_date=max_date
        )

    async def run(self):
        assert self.log is not None

        audit_logger = self.get_logger("audit_log")

        self._entries.clear()
        audit_logger("start", "")

        async with self._storage.use(self.log):
            try:
                if self.max_age_days > 0:
                    count = await self._storage.prune(days_ago(self.max_age_days))
                    if count is not None:
                        if count > 1:
                            self.log.info(f"Pruned {count} entries from the audit log")
                        elif count == 1:
                            self.log.info("Pruned one entry from the audit log")

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
schema = {
    "properties": {
        "max_age": {
            "title": "Max age (days)",
            "description": (
                "Maximum age of entries in the audit log, in days. Log entries "
                "older than the given number of days are periodically removed from "
                "the audit log. Zero means no limit."
            ),
            "type": "number",
            "minimum": 0,
            "default": 365,
        }
    }
}
