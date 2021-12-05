from math import inf
from trio import Cancelled, Event, open_memory_channel, WouldBlock
from typing import Generic, Optional, TypeVar

__all__ = ("TrioQueue",)

T = TypeVar("T")


class TrioQueue(Generic[T]):
    """Trio-based queue that provides an interface that is compatible with
    standard Python queues.
    """

    _join_event: Optional[Event]
    _maxsize: float
    _tasks_pending: int

    def __init__(self, maxsize: int = 0):
        """Constructor."""
        self._maxsize = maxsize if maxsize > 0 else inf
        self._sender, self._receiver = open_memory_channel(self._maxsize)
        self._join_event = None
        self._tasks_pending = 0

    def empty(self) -> bool:
        return self.qsize() == 0

    def full(self) -> bool:
        return self.qsize() >= self._maxsize

    async def join(self) -> None:
        if self._tasks_pending == 0 and not self._join_event:
            return
        else:
            self._ensure_join_event()
            await self._join_event.wait()  # type: ignore

    @property
    def maxsize(self) -> float:
        return self._maxsize

    async def get(self) -> T:
        return await self._receiver.receive()

    def get_nowait(self):
        return self._receiver.receive_nowait()

    async def put(self, value: T) -> None:
        self._tasks_pending += 1
        try:
            await self._sender.send(value)
        except Cancelled:
            self._tasks_pending -= 1
            raise

    def put_nowait(self, value: T) -> None:
        self._tasks_pending += 1
        try:
            self._sender.send_nowait(value)
        except WouldBlock:
            self._tasks_pending -= 1
            raise

    def qsize(self) -> int:
        return self._sender.statistics().current_buffer_used

    def task_done(self) -> None:
        self._tasks_pending -= 1
        if self._tasks_pending < 0:
            raise RuntimeError("task_done() called too many times")
        self._trigger_join_event_if_needed()

    def _ensure_join_event(self) -> None:
        if self._join_event is None:
            self._join_event = Event()
            self._trigger_join_event_if_needed()

    def _trigger_join_event_if_needed(self) -> None:
        if self._join_event and self._tasks_pending == 0:
            self._join_event.set()
            self._join_event = None
