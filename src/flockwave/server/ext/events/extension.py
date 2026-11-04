"""Extension that allows clients to subscribe to server-side events by type and
receive `EVT-INF` notifications when these events occur.

Event types are arbitrary non-empty strings, but it is strongly advised to use
the ``extension:type`` format (e.g., ``show:config``) so each extension gets its
own namespace.

Extensions that wish to dispatch events must declare a dependency on this
extension and register the event types they dispatch with the ``use()`` API
function, which returns a `Dispatcher` that can be used to dispatch events of
the given type.

Events are typically used as *invalidation* signals: they tell interested
clients that some piece of server-side state has changed so the client can
query the new state at its own convenience, using the usual request-response
messages. Events dispatched in quick succession are coalesced: events are sent
to the subscribers after a short delay, and events of the same type that were
dispatched within this window are merged according to the merging policy of the
event type. The default merging policy keeps the latest event only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator, Iterable
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any, ContextManager, Protocol

from trio import sleep
from trio_util import AsyncBool
from typing_extensions import TypeVar

from flockwave.server.ext.base import Extension
from flockwave.server.model.client import Client

from .dispatcher import Dispatcher
from .merging import MergingPolicy, keep_latest

if TYPE_CHECKING:
    from flockwave.server.message_hub import MessageHub
    from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
    from flockwave.server.registries import ClientRegistry

__all__ = ("EventsExtensionAPI", "construct", "description", "schema")


DEFAULT_COALESCING_DELAY = 0.1
"""Default number of seconds to wait after an event was dispatched before
sending the `EVT-INF` notifications to the subscribers.
"""

T = TypeVar("T", default=Any)
"""Type variable representing the type of the bodies of events."""


class EventsExtension(Extension):
    """Extension that allows clients to subscribe to server-side events by type
    and receive `EVT-INF` notifications when these events occur.

    Subscriptions are idempotent: subscribing a client to the same event type
    multiple times has the same effect as subscribing it once, and a single
    unsubscription removes the subscription. Subscriptions of a client are
    removed automatically when the client disconnects. Clients may subscribe to
    any event type, even if it is not registered (yet).
    """

    coalescing_delay: float
    """Number of seconds to wait after an event was dispatched before sending
    the `EVT-INF` notifications to the subscribers.
    """

    _dispatchers: dict[str, Dispatcher[Any]]
    """Mapping from registered event types to their dispatchers."""

    _has_pending: AsyncBool
    """Flag that is set when there is at least one pending event."""

    _pending: dict[str, list[Any]]
    """Mapping from event types to the bodies of the events that were
    dispatched but not sent to the subscribers yet. ``None`` means that the
    event has no body. The order of keys reflects the order in which the
    event types were last dispatched.
    """

    _subscribers: defaultdict[str, set[Client]]
    """Mapping from event types to the set of clients subscribed to them."""

    def __init__(self):
        super().__init__()

        self.coalescing_delay = DEFAULT_COALESCING_DELAY

        self._dispatchers = {}
        self._has_pending = AsyncBool(False)
        self._pending = {}
        self._subscribers = defaultdict(set)

    def exports(self) -> dict[str, Any]:
        return {"use": self._use}

    def handle_EVT_SUB(self, message: FlockwaveMessage, sender: Client, hub):
        return self._create_subscription_response(
            sender, message.body["types"], self.subscribe, message, hub
        )

    def handle_EVT_UNSUB(self, message: FlockwaveMessage, sender: Client, hub):
        return self._create_subscription_response(
            sender, message.body["types"], self.unsubscribe, message, hub
        )

    def has_subscribers(self, type: str) -> bool:
        """Returns whether there is at least one client subscribed to the
        given event type.
        """
        return bool(self._subscribers.get(type))

    def list_subscriptions(self, client: Client) -> list[str]:
        """Returns the sorted list of event types that the given client is
        subscribed to.
        """
        return sorted(
            type for type, clients in self._subscribers.items() if client in clients
        )

    async def run(self, app, configuration, logger):
        self.coalescing_delay = float(
            configuration.get("coalescing_delay", DEFAULT_COALESCING_DELAY)
        )

        handlers = {
            "EVT-SUB": self.handle_EVT_SUB,
            "EVT-UNSUB": self.handle_EVT_UNSUB,
        }

        with ExitStack() as stack:
            stack.enter_context(
                app.client_registry.removed.connected_to(
                    self._on_client_removed, sender=app.client_registry
                )
            )
            stack.enter_context(app.message_hub.use_message_handlers(handlers))
            await self._send_pending_events(app.message_hub)

    def subscribe(self, client: Client, type: str) -> None:
        """Subscribes the given client to the given event type.

        Subscribing to the same event type multiple times has no additional
        effect.

        Raises:
            ValueError: if the event type is invalid
        """
        _validate_type(type)
        self._subscribers[type].add(client)

    def unsubscribe(self, client: Client, type: str) -> None:
        """Unsubscribes the given client from the given event type.

        Unsubscribing from an event type that the client is not subscribed to
        has no effect.

        Raises:
            ValueError: if the event type is invalid
        """
        _validate_type(type)
        clients = self._subscribers.get(type)
        if clients is not None:
            clients.discard(client)
            if not clients:
                del self._subscribers[type]

    def _create_subscription_response(
        self,
        client: Client,
        types: Iterable[Any],
        func,
        in_response_to: FlockwaveMessage,
        hub: MessageHub,
    ) -> FlockwaveResponse:
        response = hub.create_response_or_notification(
            {}, in_response_to=in_response_to
        )
        for type in types:
            try:
                func(client, type)
            except ValueError as ex:
                response.add_error(str(type), str(ex))
            else:
                response.add_result(type, True)
        return response

    def _enqueue(self, dispatcher: Dispatcher[Any], body: Any) -> None:
        """Enqueues an event dispatched by the given dispatcher, merging it with
        the pending events of the same type according to the merging policy of
        the dispatcher.
        """
        type = dispatcher.type
        if self._dispatchers.get(type) is not dispatcher:
            return

        if not self._subscribers.get(type):
            return

        # Remove the old entry first so the order of keys in the pending
        # dict reflects the order in which the event types were last dispatched
        pending = self._pending.pop(type, [])
        merged = list(dispatcher.merging_policy(pending, body))
        if merged:
            self._pending[type] = merged
            self._has_pending.value = True

    def _flush(self, hub: MessageHub) -> None:
        """Sends the pending events to the subscribed clients and clears the
        list of pending events.
        """
        pending = self._pending
        self._pending = {}
        self._has_pending.value = False

        events_by_client: defaultdict[Client, list[dict[str, Any]]] = defaultdict(list)
        for type, bodies in pending.items():
            subscribers = self._subscribers.get(type)
            if not subscribers:
                continue

            events = [_create_event(type, body) for body in bodies]
            for client in subscribers:
                events_by_client[client].extend(events)

        for client, events in events_by_client.items():
            notification = hub.create_notification(
                {"type": "EVT-INF", "events": events}
            )
            hub.enqueue_message(notification, to=client)

    def _on_client_removed(self, sender: ClientRegistry, client: Client) -> None:
        """Handler called when a client disconnected from the server."""
        for type in list(self._subscribers):
            self.unsubscribe(client, type)

    async def _send_pending_events(self, hub: MessageHub) -> None:
        """Background task that sends the pending events to the subscribed
        clients.
        """
        while True:
            await self._has_pending.wait_value(True)
            await sleep(self.coalescing_delay)
            self._flush(hub)

    @contextmanager
    def _use(
        self, type: str, *, merging_policy: MergingPolicy[T] = keep_latest
    ) -> Generator[Dispatcher[T], None, None]:
        """Registers an event type with the given merging policy when the
        context is entered and unregisters it when the context is exited.

        Pending events of the event type that were not sent yet are dropped
        when the event type is unregistered.

        Args:
            type: the event type to register. It is advised to use the
                ``extension:type`` format.
            merging_policy: the merging policy that determines what happens
                when multiple events of this type are dispatched within the
                coalescing window

        Yields:
            a dispatcher that can be used to dispatch events of the given type

        Raises:
            ValueError: if the event type is invalid
            KeyError: if the event type is already registered
        """
        _validate_type(type)
        if type in self._dispatchers:
            raise KeyError(f"Event type already registered: {type!r}")

        dispatcher = Dispatcher[T](self, type, merging_policy)
        self._dispatchers[type] = dispatcher
        try:
            yield dispatcher
        finally:
            dispatcher._deactivate()
            del self._dispatchers[type]
            self._pending.pop(type, None)


class EventsExtensionAPI(Protocol):
    """Interface specification for the API exposed by the `events` extension."""

    def use(
        self, type: str, *, merging_policy: MergingPolicy[T] = keep_latest
    ) -> ContextManager[Dispatcher[T]]: ...


def _create_event(type: str, body: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"type": type}
    if body is not None:
        event["body"] = body
    return event


def _validate_type(type: Any) -> None:
    if not isinstance(type, str) or not type:
        raise ValueError("Event type must be a non-empty string")


construct = EventsExtension
description = "Subscription to server-side events for clients"
schema = {
    "properties": {
        "coalescing_delay": {
            "type": "number",
            "title": "Coalescing delay (seconds)",
            "description": (
                "Number of seconds to wait after an event was dispatched before "
                "sending it to the subscribed clients. Events of the same type "
                "that are dispatched within this window are merged according to "
                "the merging policy of the event type."
            ),
            "minimum": 0,
            "default": DEFAULT_COALESCING_DELAY,
        }
    }
}
tags = ("system",)
