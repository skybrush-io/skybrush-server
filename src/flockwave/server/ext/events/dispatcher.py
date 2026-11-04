"""Dispatcher objects that extensions can use to dispatch events of a single
type via the `events` extension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic

from typing_extensions import TypeVar

from .merging import MergingPolicy

if TYPE_CHECKING:
    from .extension import EventsExtension

__all__ = ("Dispatcher",)


T = TypeVar("T", default=Any)
"""Type variable representing the type of the bodies of the events dispatched
by a dispatcher.
"""


class Dispatcher(Generic[T]):
    """Object that can be used to dispatch events of a single type.

    Dispatchers are created by the ``use()`` API function of the `events`
    extension. A dispatcher becomes inactive when the context created by
    ``use()`` is exited; dispatching events from an inactive dispatcher has no
    effect.

    The type parameter of the dispatcher is the type of the bodies of the
    events that it dispatches. Events may also be dispatched without a body,
    in which case the body is ``None``.
    """

    _extension: EventsExtension | None
    _merging_policy: MergingPolicy[T]
    _type: str

    def __init__(
        self,
        extension: EventsExtension,
        type: str,
        merging_policy: MergingPolicy[T],
    ):
        """Constructor.

        Args:
            extension: the extension that the dispatcher belongs to
            type: the type of the events dispatched by this dispatcher
            merging_policy: the merging policy of the event type
        """
        self._extension = extension
        self._merging_policy = merging_policy
        self._type = type

    @property
    def active(self) -> bool:
        """Whether the dispatcher is still active, i.e. whether its event type
        is still registered.
        """
        return self._extension is not None

    @property
    def has_subscribers(self) -> bool:
        """Whether there is at least one client subscribed to the events of
        this dispatcher. Can be used to skip constructing expensive event
        bodies if nobody is interested in them.
        """
        return self._extension is not None and self._extension.has_subscribers(
            self._type
        )

    @property
    def merging_policy(self) -> MergingPolicy[T]:
        """The merging policy of the event type of this dispatcher."""
        return self._merging_policy

    @property
    def type(self) -> str:
        """The type of the events dispatched by this dispatcher."""
        return self._type

    def dispatch(self, body: T | None = None) -> None:
        """Dispatches an event to all clients that are subscribed to the event
        type of this dispatcher.

        The event is not sent immediately; it is sent after a short coalescing
        delay, and it may be merged with other events of the same type that
        were dispatched in the meanwhile, according to the merging policy of
        the event type.

        Args:
            body: optional JSON-serializable body of the event
        """
        if self._extension is not None:
            self._extension._enqueue(self, body)

    __call__ = dispatch

    def _deactivate(self) -> None:
        self._extension = None
