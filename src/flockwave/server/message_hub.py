"""Classes related to message handling in Flockwave."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections import defaultdict
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from functools import partial
from inspect import isawaitable
from itertools import chain
from logging import Logger
from jsonschema import ValidationError
from time import monotonic
from trio import (
    BrokenResourceError,
    ClosedResourceError,
    Event,
    MemoryReceiveChannel,
    MemorySendChannel,
    move_on_after,
    open_memory_channel,
    open_nursery,
    sleep,
)
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    Iterator,
    Optional,
    TypeVar,
    Union,
    overload,
)

from flockwave.connections import ConnectionState
from flockwave.concurrency import AsyncBundler

from .logger import log as base_log
from .middleware import RequestMiddleware, ResponseMiddleware
from .middleware.logging import RequestLogMiddleware, ResponseLogMiddleware
from .model import (
    Client,
    FlockwaveMessage,
    FlockwaveMessageBuilder,
    FlockwaveNotification,
    FlockwaveResponse,
)
from .registries import ChannelTypeRegistry, ClientRegistry
from .types import Disposer

# Legacy imports for compatibility reasons. We can get rid of these when the
# "dock" extension has migrated to the new location in .message_handlers
from .message_handlers import (
    create_mapper as create_generic_INF_or_PROPS_message_factory,
    create_multi_object_message_handler,
)

__all__ = (
    "ConnectionStatusMessageRateLimiter",
    "UAVMessageRateLimiter",
    "MessageHandler",
    "MessageHandlerResponse",
    "MessageHub",
    "RateLimiters",
    "create_generic_INF_or_PROPS_message_factory",
    "create_multi_object_message_handler",
)

log: Logger = base_log.getChild("message_hub")


MessageHandlerResponse = Union[FlockwaveMessage, bool, dict]
"""Type specification for objects that we can return from a message handler
function.
"""

MessageHandler = Callable[
    [FlockwaveMessage, Client, "MessageHub"],
    Union[MessageHandlerResponse, Awaitable[MessageHandlerResponse]],
]
"""Type specification for message handler functions that take a message, a
client that the message came from, and the message hub, and returns a
response object (a message, a boolean that encodes whether the message was
handled by the handler or not, or a dictionary that is turned into a message).
"""

T = TypeVar("T")


class MessageValidationError(RuntimeError):
    """Error that is thrown by the MessageHub_ class internally when it
    fails to validate an incoming message against the Flockwave schema.
    """

    pass


@dataclass
class Request:
    """Single message sending request in the queue of the message hub."""

    #: The message to send
    message: FlockwaveMessage

    #: The client that will receive the message; `None` means that it is a
    #: broadcast
    to: Optional[Union[str, Client]] = None

    #: Another, optional message that this message responds to
    in_response_to: Optional[FlockwaveMessage] = None

    #: Whether the request has been fulfilled
    fulfilled: bool = False

    #: Event that will be dispatched when the request is processed by the
    #: outbound queue of the message hub. Constructed lazily.
    event: Optional[Event] = None

    def notify_sent(self) -> None:
        """Marks the request as fulfilled."""
        self.fulfilled = True
        if self.event is not None:
            self.event.set()

    async def wait_until_sent(self) -> None:
        """Waits until the request has been served from the outbound message
        queue.
        """
        if self.fulfilled:
            return

        if self.event is None:
            self.event = Event()
        await self.event.wait()


class MessageHub:
    """Central entity in a Flockwave server that handles incoming messages
    and prepares outbound messages for dispatch.

    Users of this class can register message handlers that will be invoked
    for incoming messages based on the type of the incoming message using
    the `register_message_handler()`_ method. The method allows one to
    register specific handlers for one or more message types as well as
    generic message handlers that are invoked for all messages. Specific
    handlers are always invoked before generic ones; otherwise the handlers
    are called in the order they were registered.

    Message handler functions are required to return one of the following
    values:

    - ``False`` or ``None`` if they decided not to handle the message they
      were given.

    - ``True`` to indicate that they have handled the message and no further
      action is needed

    - A ``dict`` that contains a response body that should be sent back to
      the caller. In this case, the hub will wrap the body in an appropriate
      message envelope that refers to the ID of the message that the
      object responds to.

    - A FlockwaveResponse_ object that is *already* constructed in a way
      that it responds to the original message.

    When the body of the response returned from the handler does not contain
    a message type (i.e. a ``type`` key), it will be added automatically,
    assuming that it is equal to the type of the incoming message.
    """

    _broadcast_methods: Optional[list[Callable[[FlockwaveMessage], Awaitable[None]]]]
    _channel_type_registry: Optional[ChannelTypeRegistry]
    _client_registry: Optional[ClientRegistry]
    _handlers_by_type: defaultdict[Optional[str], list[MessageHandler]]
    _log_messages: bool
    _message_builder: FlockwaveMessageBuilder
    _request_middleware: list[RequestMiddleware]
    _response_middleware: list[ResponseMiddleware]
    _queue_rx: MemoryReceiveChannel
    _queue_tx: MemorySendChannel

    def __init__(self):
        """Constructor."""
        self._handlers_by_type = defaultdict(list)
        self._message_builder = FlockwaveMessageBuilder()
        self._request_middleware = []
        self._response_middleware = []
        self._broadcast_methods = None
        self._channel_type_registry = None
        self._client_registry = None
        self._log_messages = False

        self._queue_tx, self._queue_rx = open_memory_channel(4096)

        if self._log_messages:
            self.register_request_middleware(RequestLogMiddleware(log))
            self.register_response_middleware(ResponseLogMiddleware(log))

    def acknowledge(
        self,
        message: Optional[FlockwaveMessage] = None,
        outcome: bool = True,
        reason: Optional[str] = None,
    ) -> FlockwaveResponse:
        """Creates a new positive or negative acknowledgment of the given
        message.

        Parameters:
            message: the message to respond to with a positive or negative
                acknowledgment
            outcome: ``True`` to construct a positive acknowledgment, ``False``
                otherwise.
            reason: the reason for a negative acknowledgment; ignored for
                positive acknowledgments

        Returns:
            a response object that acknowledges the given message
        """
        body = {"type": "ACK-ACK" if outcome else "ACK-NAK"}
        if not outcome and reason:
            body["reason"] = reason
        return self._message_builder.create_response_to(message, body)

    async def broadcast_message(self, message: FlockwaveNotification) -> Request:
        """Sends a broadcast message from this message hub.

        Blocks until the message was actually broadcast to all connected
        clients. If you are not interested in when the message is
        sent, use `enqueue_broadcast_message()` instead.

        Parameters:
            message: the notification to broadcast.

        Returns:
            the request object that identifies this message in the outbound
            message queue. It can be used to wait until the message is delivered
        """
        assert isinstance(
            message, FlockwaveNotification
        ), "only notifications may be broadcast"

        request = Request(message)
        await self._queue_tx.send(request)  # type: ignore
        return request

    @property
    def channel_type_registry(self) -> Optional[ChannelTypeRegistry]:
        """Registry that keeps track of the different channel types that the
        app can handle. This is used by the message hub to figure out how to
        broadcast messages to all connected clients.
        """
        return self._channel_type_registry

    @channel_type_registry.setter
    def channel_type_registry(self, value: Optional[ChannelTypeRegistry]) -> None:
        if self._channel_type_registry == value:
            return

        if self._channel_type_registry is not None:
            self._channel_type_registry.added.disconnect(
                self._invalidate_broadcast_methods, sender=self._channel_type_registry
            )
            self._channel_type_registry.removed.disconnect(
                self._invalidate_broadcast_methods, sender=self._channel_type_registry
            )

        self._channel_type_registry = value

        if self._channel_type_registry is not None:
            self._channel_type_registry.added.connect(
                self._invalidate_broadcast_methods, sender=self._channel_type_registry
            )
            self._channel_type_registry.removed.connect(
                self._invalidate_broadcast_methods, sender=self._channel_type_registry
            )

    @property
    def client_registry(self) -> Optional[ClientRegistry]:
        """Registry that keeps track of connected clients so the message hub
        can broadcast messages to all connected clients.
        """
        return self._client_registry

    @client_registry.setter
    def client_registry(self, value: Optional[ClientRegistry]) -> None:
        if self._client_registry == value:
            return

        if self._client_registry is not None:
            self._client_registry.added.disconnect(
                self._invalidate_broadcast_methods, sender=self._client_registry
            )
            self._client_registry.removed.disconnect(
                self._invalidate_broadcast_methods, sender=self._client_registry
            )

        self._client_registry = value

        if self._client_registry is not None:
            self._client_registry.added.connect(
                self._invalidate_broadcast_methods, sender=self._client_registry
            )
            self._client_registry.removed.connect(
                self._invalidate_broadcast_methods, sender=self._client_registry
            )

    def create_notification(self, body: Any = None) -> FlockwaveNotification:
        """Creates a new Flockwave notification to be sent by the server.

        Parameters:
            body: the body of the notification.

        Returns:
            a notification object
        """
        return self._message_builder.create_notification(body)

    @overload
    def create_response_or_notification(
        self, body: Any, in_response_to: None = None
    ) -> FlockwaveNotification:
        ...

    @overload
    def create_response_or_notification(
        self, body: Any, in_response_to: FlockwaveMessage
    ) -> FlockwaveResponse:
        ...

    def create_response_or_notification(
        self, body: Any, in_response_to: Optional[FlockwaveMessage] = None
    ) -> Union[FlockwaveResponse, FlockwaveNotification]:
        """Creates a new Flockwave response or notification object,
        depending on whether the caller specifies a message to respond to
        or not.

        Parameters:
            body: the body of the response.
            message: the message to respond to or ``None`` if we want to create
                a notification instead.

        Returns:
            FlockwaveMessage: a response or notification object
        """
        if in_response_to is None:
            return self.create_notification(body)
        else:
            return self.create_response_to(in_response_to, body)

    def create_response_to(
        self, message: FlockwaveMessage, body: Any = None
    ) -> FlockwaveResponse:
        """Creates a new Flockwave response object that will respond to the
        given message.

        Parameters:
            message: the message to respond to
            body: the body of the response.

        Returns:
            a response object that will respond to the given message
        """
        return self._message_builder.create_response_to(message, body)

    def enqueue_broadcast_message(self, message: FlockwaveNotification) -> None:
        """Enqueues a broadcast message in this message hub to be sent later.

        Broadcast messages are sent to all connected clients.

        Note that this function may drop messages if they are enqueued too
        fast. Use `broadcast_message()` if you want to block until the message
        is actually in the queue.

        Parameters:
            message: the notification to enqueue
        """
        assert isinstance(
            message, FlockwaveNotification
        ), "only notifications may be broadcast"

        # Don't return the request here because it is not guaranteed that it
        # ends up in the queue; it may be dropped
        self._queue_tx.send_nowait(Request(message))

    def enqueue_message(
        self,
        message: Union[FlockwaveMessage, dict[str, Any]],
        to: Optional[Union[str, Client]] = None,
        in_response_to: Optional[FlockwaveMessage] = None,
    ) -> None:
        """Enqueues a message or notification in this message hub to be
        sent later.

        Notifications are sent to all connected clients, unless ``to`` is
        specified, in which case they are sent only to the given client.

        Messages are sent only to the client whose request is currently
        being served, unless ``to`` is specified, in which case they are
        sent only to the given client.

        Note that this function may drop messages if they are enqueued too
        fast. Use `send_message()` if you want to block until the message
        is actually in the queue.

        Parameters:
            message: the message to enqueue, or its body (in which case
                an appropriate envelope will be added automatically)
            to: the Client_ object that represents the recipient of the message,
                or the ID of the client. ``None`` means to send the message to
                all connected clients.
            in_response_to: the message that the message being sent responds to.
        """
        if not isinstance(message, FlockwaveMessage):
            message = self.create_response_or_notification(
                message, in_response_to=in_response_to
            )
        if to is None:
            assert isinstance(message, FlockwaveNotification), (
                "broadcast messages cannot be sent in response to a "
                "particular message"
            )
            return self.enqueue_broadcast_message(message)
        else:
            # Don't return the request here because it is not guaranteed that it
            # ends up in the queue; it may be dropped
            self._queue_tx.send_nowait(
                Request(message, to=to, in_response_to=in_response_to)
            )

    async def handle_incoming_message(
        self, message: dict[str, Any], sender: Client
    ) -> bool:
        """Handles an incoming Flockwave message by calling the appropriate
        message handlers.

        Parameters:
            message: the incoming message, already decoded from its string
                representation into a Python dict, but before it was validated
                against the Flockwave schema
            sender: the sender of the message

        Returns:
            bool: whether the message was handled by at least one handler
                or internally by the hub itself
        """
        try:
            decoded_message = self._decode_incoming_message(message)
        except MessageValidationError as ex:
            reason = str(ex)
            log.error(
                reason, extra={"id": str(message.get("body", {}).get("type", ""))}
            )
            if "id" in message:
                ack = self.reject(message, reason=reason)
                await self.send_message(ack, to=sender)
                return True
            else:
                return False

        try:
            for middleware in self._request_middleware:
                next_message = middleware(decoded_message, sender)
                if next_message is None:
                    # Message dropped by middleware
                    return True

                decoded_message = next_message
        except Exception:
            log.exception("Unexpected error in request middleware")
            return False

        handled = await self._feed_message_to_handlers(decoded_message, sender)

        if not handled:
            message_type = decoded_message.get_type()
            if message_type and message_type not in ("BCN-INF", "DOCK-INF", "MSN-INF"):
                # Do not log these messages; these may come from Skybrush
                # Live but we do not want to freak out the user watching the
                # server logs
                log.warning(
                    f"Unhandled message: {message_type}",
                    extra={"id": decoded_message.id},
                )

            ack = self.reject(
                decoded_message,
                reason="No handler managed to parse this message in the server",
            )
            await self.send_message(ack, to=sender)

            return False

        return True

    async def iterate(
        self, *args
    ) -> AsyncIterator[tuple[dict[str, Any], Client, Callable[[Dict], None]]]:
        """Returns an async generator that yields triplets consisting of
        the body of an incoming message, its sender and an appropriate function
        that can be used to respond to that message.

        The generator yields triplets containing only those messages whose type
        matches the message types provided as arguments.

        Messages can be responded to by calling the responder function with
        the response itself. The responder function returns immediately after
        queueing the message for dispatch; it does not wait for the message
        to actually be dispatched.

        It is assumed that all messages are handled by the consumer of the
        generator; it is not possible to leave messages unhandled. Not sending
        a response to a message is okay as long as it is allowed by the
        communication protocol.

        Yields:
            the body of an incoming message, its sender and the responder
            function
        """
        to_handlers, from_clients = open_memory_channel(0)

        async def handle(
            message: FlockwaveMessage, sender: Client, hub: "MessageHub"
        ) -> bool:
            await to_handlers.send((message, sender))
            return True

        with self.use_message_handler(handle, args):
            while True:
                message, sender = await from_clients.receive()
                if message.body:
                    responder = partial(
                        self.enqueue_message, to=sender, in_response_to=message
                    )
                    yield message.body, sender, responder

    def _commit_broadcast_methods(
        self,
    ) -> list[Callable[[FlockwaveMessage], Awaitable[None]]]:
        """Calculates the list of methods to call when the message hub
        wishes to broadcast a message to all the connected clients.
        """
        assert (
            self._client_registry is not None
        ), "message hub does not have a client registry yet"
        assert (
            self._channel_type_registry is not None
        ), "message hub does not have a channel type registry yet"

        result = []
        clients_for = self._client_registry.client_ids_for_channel_type
        has_clients_for = self._client_registry.has_clients_for_channel_type

        for channel_type_id in self._channel_type_registry.ids:
            descriptor = self._channel_type_registry[channel_type_id]
            broadcaster = descriptor.broadcaster
            if broadcaster:
                if has_clients_for(descriptor.id):
                    result.append(broadcaster)
            else:
                clients = clients_for(descriptor.id)
                for client_id in clients:
                    result.append(partial(self._send_message, to=client_id))

        return result

    async def _feed_message_to_handlers(
        self, message: FlockwaveMessage, sender: Client
    ) -> bool:
        """Forwards an incoming, validated Flockwave message to the message
        handlers registered in the message hub.

        Parameters:
            message: the message to process
            sender: the sender of the message

        Returns:
            whether the message was handled by at least one handler
        """
        # TODO(ntamas): right now the handlers are executed in sequence so
        # one handler could arbitrarily delay the ones coming later in the
        # queue

        message_type = message.body["type"]
        all_handlers = chain(
            self._handlers_by_type.get(message_type, ()), self._handlers_by_type[None]
        )

        handled = False
        for handler in all_handlers:
            try:
                response = handler(message, sender, self)
            except Exception:
                log.exception(
                    "Error while calling handler {0!r} "
                    "for incoming message; proceeding with "
                    "next handler (if any)".format(handler)
                )
                response = None

            if isawaitable(response):
                try:
                    response = await response
                except Exception:
                    log.exception(
                        "Error while waiting for response from handler "
                        "{0!r} for incoming message; proceeding with "
                        "next handler (if any)".format(handler)
                    )
                    response = None

            if response is True:
                # Message was handled by the handler
                handled = True
            elif response is False or response is None:
                # Message was rejected by the handler, nothing to do
                pass
            elif isinstance(response, (dict, FlockwaveResponse)):
                # Handler returned a dict or a response; we must enqueue it
                # for later dispatch. (We cannot send it immediately due to
                # ordering constraints; e.g., async operation notifications
                # must be sent later than the initial responses because the
                # latter contain the receipt IDs that the former ones refer to).
                self.enqueue_message(response, to=sender, in_response_to=message)
                handled = True

        return handled

    def _invalidate_broadcast_methods(self, *args, **kwds):
        """Invalidates the list of methods to call when the message hub
        wishes to broadcast a message to all the connected clients.
        """
        self._broadcast_methods = None

    def on(self, *args: str) -> Callable[[MessageHandler], MessageHandler]:
        """Decorator factory function that allows one to register a message
        handler on a MessageHub_ with the following syntax::

            @message_hub.on("SYS-VER")
            def handle_SYS_VER(message, sender, hub):
                [...]
        """

        def decorator(func: MessageHandler) -> MessageHandler:
            self.register_message_handler(func, args)
            return func

        return decorator

    def register_message_handler(
        self, func: MessageHandler, message_types: Optional[Iterable[str]] = None
    ) -> Disposer:
        """Registers a handler function that will handle incoming messages.

        It is possible to register the same handler function multiple times,
        even for the same message type.

        Parameters:
            func: the handler to register. It will be called with the incoming
                message and the message hub object.
            message_types: an iterable that yields the message types for which
                this handler will be registered. ``None`` means to register the
                handler for all message types. The handler function must return
                ``True`` if it has handled the message successfully, ``False``
                if it skipped the message. Note that returning ``True`` will not
                prevent other handlers from getting the message.

        Returns:
            a function that can be called with no arguments to unregister the
            handler function from the given message types
        """
        message_type_list: Iterable[Optional[str]]

        if message_types is None or isinstance(message_types, str):
            message_type_list = [message_types]
        else:
            message_type_list = message_types

        for message_type in message_type_list:
            if message_type is not None and not isinstance(message_type, str):
                message_type = message_type.decode("utf-8")
            self._handlers_by_type[message_type].append(func)

        return partial(self.unregister_message_handler, func, message_types)

    def register_request_middleware(
        self, middleware: RequestMiddleware, where: str = "post"
    ) -> Disposer:
        """Registers a request middleware that the incoming requests will
        pass through. Request middleware may modify, log or filter incoming
        requests as needed before it reaches the corresponding message handler.

        Parameters:
            middleware: the middleware to register. It will be called with
                an incoming message and the client that sent the message, and
                must return the same message (to pass it through intact),
                a modified message or ``None`` to prevent the message from
                reaching other middleware and the message handlers.
            where: specifies whether the middleware should be registered
                _before_ existing middleware (``pre``) or _after_ them
                (``post``).

        Raises:
            ValueError: if the middleware is already registered
        """
        if middleware in self._request_middleware:
            raise ValueError("middleware is already registered")

        if where == "pre":
            self._request_middleware.insert(0, middleware)
        elif where == "post":
            self._request_middleware.append(middleware)
        else:
            raise ValueError(f"unknown middleware position: {where!r}")

        return partial(self.unregister_request_middleware, middleware)

    def register_response_middleware(
        self, middleware: ResponseMiddleware, where: str = "post"
    ) -> Disposer:
        """Registers a response middleware that the outbound responses,
        notifications and broadcasts will pass through. Response middleware may
        modify, log or filter outbound messages as needed before they are
        dispatched to their intended recipients.

        Parameters:
            middleware: the middleware to register. It will be called with
                an outbound message, an optional client that the message is
                targeted to (if it is not a broadcast) and an optional additional
                message that this message is a reply to. The client and the
                additional message may be ``None``. The middleware must
                must return the same outbound message (to pass it through intact),
                a modified message or ``None`` to prevent the message from
                being sent to its intended recipients.
            where: specifies whether the middleware should be registered
                _before_ existing middleware (``pre``) or _after_ them
                (``post``).

        Raises:
            ValueError: if the middleware is already registered
        """
        if middleware in self._response_middleware:
            raise ValueError("middleware is already registered")

        if where == "pre":
            self._response_middleware.insert(0, middleware)
        elif where == "post":
            self._response_middleware.append(middleware)
        else:
            raise ValueError(f"unknown middleware position: {where!r}")

        return partial(self.unregister_response_middleware, middleware)

    def reject(
        self,
        message: Optional[Union[dict[str, Any], FlockwaveMessage]] = None,
        reason: Optional[str] = None,
    ) -> FlockwaveResponse:
        """Creates a new negative acknowledgment (i.e. rejection) of the given
        message.

        Parameters:
            message: the message to respond to with a negative acknowledgment
            reason: the reason for the negative acknowledgment

        Returns:
            a response object that negatively acknowledges (i.e. rejects) the
                given message
        """
        body = {"type": "ACK-NAK"}
        if reason:
            body["reason"] = reason
        return self._message_builder.create_response_to(message, body)

    async def run(self) -> None:
        """Runs the message hub in an infinite loop. This method should be
        launched in a Trio nursery.
        """
        async with open_nursery() as nursery, self._queue_rx:
            async for request in self._queue_rx:
                if request.to:
                    nursery.start_soon(
                        self._send_message,
                        request.message,
                        request.to,
                        request.in_response_to,
                        request.notify_sent,
                    )
                else:
                    nursery.start_soon(
                        self._broadcast_message, request.message, request.notify_sent
                    )

    async def send_message(
        self,
        message: Union[FlockwaveMessage, dict[str, Any]],
        to: Optional[Union[str, Client]] = None,
        in_response_to: Optional[FlockwaveMessage] = None,
    ) -> Request:
        """Sends a message or notification from this message hub.

        Notifications are sent to all connected clients, unless ``to`` is
        specified, in which case they are sent only to the given client.

        Messages are sent only to the client whose request is currently
        being served, unless ``to`` is specified, in which case they are
        sent only to the given client.

        Parameters:
            message: the message to send.
            to: the Client_ object that represents the recipient of the message,
                or the ID of the client. ``None`` means to send the message to
                all connected clients.
            in_response_to: the message that the message being sent responds to.

        Returns:
            the request object that identifies this message in the outbound
            message queue. It can be used to wait until the message is delivered
        """
        if not isinstance(message, FlockwaveMessage):
            message = self.create_response_or_notification(
                message, in_response_to=in_response_to
            )
        if to is None:
            assert isinstance(message, FlockwaveNotification), (
                "broadcast messages cannot be sent in response to a "
                "particular message"
            )
            return await self.broadcast_message(message)

        request = Request(message, to=to, in_response_to=in_response_to)
        await self._queue_tx.send(request)  # type: ignore
        return request

    def unregister_message_handler(
        self, func: MessageHandler, message_types: Optional[Iterable[str]] = None
    ) -> None:
        """Unregisters a handler function from the given message types.

        Parameters:
            func: the handler to unregister.
            message_types: an iterable that yields the message types from which
                this handler will be unregistered. ``None`` means to unregister
                the handler from all message types; however, if it was also
                registered for specific message types individually (in addition
                to all messages in general), it will also have to be unregistered
                from the individual message types.
        """
        message_type_list: Iterable[Optional[str]]

        if message_types is None or isinstance(message_types, str):
            message_type_list = [message_types]
        else:
            message_type_list = message_types

        for message_type in message_type_list:
            if message_type is not None and not isinstance(message_type, str):
                message_type = message_type.decode("utf-8")
            handlers = self._handlers_by_type.get(message_type)
            if handlers:
                try:
                    handlers.remove(func)
                except ValueError:
                    # Handler not in list; no problem
                    pass

    def unregister_request_middleware(self, middleware: RequestMiddleware) -> None:
        """Unregisters the given request middleware from the middleware chain.

        This function is a no-op if the middleware is not in the middleware
        chain.
        """
        try:
            self._request_middleware.remove(middleware)
        except ValueError:
            pass

    def unregister_response_middleware(self, middleware: ResponseMiddleware) -> None:
        """Unregisters the given response middleware from the middleware chain.

        This function is a no-op if the middleware is not in the middleware
        chain.
        """
        try:
            self._response_middleware.remove(middleware)
        except ValueError:
            pass

    @contextmanager
    def use_message_handler(
        self, func: MessageHandler, message_types: Optional[Iterable[str]] = None
    ) -> Iterator[None]:
        """Context manager that registers a handler function that will handle
        incoming messages, and unregisters the function upon exiting the
        context.

        Parameters:
            func: the handler to register. It will be called with the incoming
                message, the sender and the message hub object.

            message_types: an iterable that yields the message types for which
                this handler will be registered. ``None`` means to register the
                handler for all message types. The handler function must return
                ``True`` if it has handled the message successfully, ``False``
                if it skipped the message. Note that returning ``True`` will not
                prevent other handlers from getting the message.
        """
        disposer = self.register_message_handler(func, message_types)
        try:
            yield
        finally:
            disposer()

    @contextmanager
    def use_message_handlers(
        self, handlers: dict[str, MessageHandler]
    ) -> Iterator[None]:
        """Context manager that registers multiple handler functions, specified
        in a dictionary mapping message types to handlers, and then unregisters
        the functions upon exiting the context.

        Parameters:
            handlers: the handlers to register. It must be a dictionary mapping
                message types to their handler functions. Each handler will be
                called with the incoming message, the sender and the message
                hub object.
        """
        with ExitStack() as stack:
            for message_type, handler in handlers.items():
                disposer = self.register_message_handler(handler, [message_type])
                stack.callback(disposer)
            yield

    @contextmanager
    def use_request_middleware(self, middleware: RequestMiddleware) -> Iterator[None]:
        """Context manager that registers a request middleware when entering
        the context, and unregisters it when exiting the context.

        Parameters:
            middleware: the middleware to register.
        """
        disposer = self.register_request_middleware(middleware)
        try:
            yield
        finally:
            disposer()

    @contextmanager
    def use_response_middleware(self, middleware: ResponseMiddleware) -> Iterator[None]:
        """Context manager that registers a response middleware when entering
        the context, and unregisters it when exiting the context.

        Parameters:
            middleware: the middleware to register.
        """
        disposer = self.register_response_middleware(middleware)
        try:
            yield
        finally:
            disposer()

    def _decode_incoming_message(self, message: dict[str, Any]) -> FlockwaveMessage:
        """Decodes an incoming, raw JSON message that has already been
        decoded from the string representation into a dictionary on the
        Python side, but that has not been validated against the Flockwave
        message schema.

        Parameters:
            message: the incoming, raw message

        Returns:
            the validated message as a Python FlockwaveMessage_ object

        Raises:
            MessageValidationError: if the message could not have been decoded
        """
        try:
            return FlockwaveMessage.from_json(message)  # type: ignore
        except ValidationError:
            # We should not re-raise directly from here because on Python 3.x
            # we would get a very long stack trace that includes the original
            # exception as well.
            if FlockwaveMessage.is_experimental(message):
                try:
                    return FlockwaveMessage.from_json(message, validate=False)  # type: ignore
                except Exception as ex:
                    error = MessageValidationError(
                        "Unexpected exception: {0!r}".format(ex)
                    )
            else:
                error = MessageValidationError(
                    "Flockwave message does not match schema"
                )
        except Exception as ex:
            # We should not re-raise directly from here because on Python 3.x
            # we would get a very long stack trace that includes the original
            # exception as well.
            error = MessageValidationError("Unexpected exception: {0!r}".format(ex))
        raise error

    async def _broadcast_message(
        self, message: FlockwaveNotification, done: Callable[[], None]
    ) -> None:
        if self._broadcast_methods is None:
            self._broadcast_methods = self._commit_broadcast_methods()

        if self._broadcast_methods:
            for middleware in self._response_middleware:
                try:
                    next_message = middleware(message, None, None)
                except Exception:
                    log.exception("Unexpected error in response middleware")
                    next_message = None
                if next_message is None:
                    # Message dropped by middleware
                    break
                message = next_message  # type: ignore
            else:
                # Message passed through all middleware
                failures = 0
                for func in self._broadcast_methods:
                    try:
                        await func(message)
                    except (BrokenResourceError, ClosedResourceError):
                        # client is probably gone; no problem
                        pass
                    except Exception:
                        failures += 1

                if failures > 0:
                    log.error(
                        f"Error while broadcasting message to {failures} client(s)"
                    )

        done()

    async def _send_message(
        self,
        message: FlockwaveMessage,
        to: Union[str, Client],
        in_response_to: Optional[FlockwaveMessage] = None,
        done: Optional[Callable[[], None]] = None,
    ):
        assert (
            self._client_registry is not None
        ), "message hub does not have a client registry yet"

        if not isinstance(to, Client):
            try:
                client = self._client_registry[to]
            except KeyError:
                log.warning(
                    "Client is gone; not sending message", extra={"id": str(to)}
                )
                if done:
                    done()
                return
        else:
            client = to

        for middleware in self._response_middleware:
            try:
                next_message = middleware(message, client, in_response_to)
            except Exception:
                log.exception("Unexpected error in response middleware")
                next_message = None
            if next_message is None:
                # Message dropped by middleware
                break
            message = next_message
        else:
            # Message passed through all middleware
            try:
                await client.channel.send(message)
            except (BrokenResourceError, ClosedResourceError):
                log.warning(
                    "Client is gone; not sending message", extra={"id": client.id}
                )
            except Exception:
                log.exception(
                    "Error while sending message to client", extra={"id": client.id}
                )
            else:
                if hasattr(message, "_notify_sent"):
                    message._notify_sent()  # type: ignore
                if done:
                    done()

    async def _send_response(
        self, message, to: Client, in_response_to: FlockwaveMessage
    ) -> Optional[FlockwaveResponse]:
        """Sends a response to a message from this message hub.

        Parameters:
            message: the response, or the body of the response. When it is a
                FlockwaveResponse_ object, the function will check whether the
                response indeed refers to the given message (in the
                ``in_response_to`` parameter). When it is any other object, it
                will be wrapped in a FlockwaveResponse_ object first. In both
                cases, the type of the message body will be filled from the type
                of the original message if it is not given.
            to: the intended recipient of the message.
            in_response_to: the message that the given object is responding to

        Returns:
            the response that was sent back to the client, or `None` if the
            response could not have been created for some reason
        """
        if isinstance(message, FlockwaveResponse):
            assert message.refs == in_response_to.id
            response = message
        else:
            try:
                response = self._message_builder.create_response_to(
                    in_response_to, body=message
                )
            except Exception:
                log.exception("Failed to create response")
                response = None

        if response:
            await self.send_message(response, to=to, in_response_to=in_response_to)
        return response


##############################################################################


class RateLimiter(metaclass=ABCMeta):
    """Abstract base class for rate limiter objects."""

    name: Optional[str] = None

    @abstractmethod
    def add_request(self, *args, **kwds) -> None:
        """Adds a new request to the rate limiter.

        The interpretation of positional and keyword arguments must be
        specialized and described in implementations of the RateLimiter_
        interface.
        """
        raise NotImplementedError

    @abstractmethod
    async def run(self, dispatcher, nursery) -> None:
        """Runs the task handling the messages emitted from this rate
        limiter.
        """
        raise NotImplementedError


@dataclass
class BatchMessageRateLimiter(RateLimiter, Generic[T]):
    """Rate limiter that collects incoming message dispatch requests in a list
    and releases multiple messages at once in a single message, ensuring a
    minimum delay between consecutive message dispatches.

    The rate limiter requires a factory function that takes a list of requests
    and produces a single FlockwaveMessage_ to send.
    """

    factory: Callable[[Iterable[T]], FlockwaveMessage]
    name: Optional[str] = None
    delay: float = 0.1

    bundler: AsyncBundler = field(default_factory=AsyncBundler)

    def add_request(self, request: T) -> None:
        self.bundler.add(request)

    async def run(self, dispatcher, nursery):
        self.bundler.clear()
        async with self.bundler.iter() as bundle_iterator:
            async for bundle in bundle_iterator:
                try:
                    await dispatcher(self.factory(bundle))
                except Exception:
                    log.exception(
                        f"Error while dispatching messages from {self.name} factory"
                    )
                await sleep(self.delay)


@dataclass
class UAVMessageRateLimiter(RateLimiter):
    """Generic rate limiter that is useful for most types of UAV-related
    messages that we want to rate-limit.

    The rate limiter receives requests containing lists of UAV IDs; these are
    the UAVs for which we want to send a specific type of message (say,
    UAV-INF). The first request is always executed immediately. For all
    subsequent requests, the rate limiter checks the amount of time elapsed
    since the last message dispatch. If it is greater than a prescribed delay,
    the message is sent immediately; otherwise the rate limiter waits and
    collects all UAV IDs for which a request arrives until the delay is reached,
    and _then_ sends a single message containing information about all UAVs
    that were referred recently.

    The rate limiter requires a factory function that takes a list of UAV IDs
    and produces a single FlockwaveMessage_ to send.
    """

    factory: Callable[[Iterable[str]], FlockwaveMessage]
    name: Optional[str] = None
    delay: float = 0.1

    bundler: AsyncBundler = field(default_factory=AsyncBundler)

    def add_request(self, uav_ids: Iterable[str]) -> None:
        """Requests that the task handling the messages for this factory
        send the messages corresponding to the given UAV IDs as soon as
        the rate limiting rules allow it.
        """
        self.bundler.add_many(uav_ids)

    async def run(self, dispatcher, nursery):
        self.bundler.clear()
        async with self.bundler.iter() as bundle_iterator:
            async for bundle in bundle_iterator:
                try:
                    await dispatcher(self.factory(bundle))
                except Exception:
                    log.exception(
                        f"Error while dispatching messages from {self.name} factory"
                    )
                await sleep(self.delay)


class ConnectionStatusMessageRateLimiter(RateLimiter):
    """Specialized rate limiter for CONN-INF (connection status) messages.

    For this rate limiter, the rules are as follows. Each request must convey
    a single connection ID and the corresponding new status of the connection
    that we wish to inform listeners about. When the new status is a stable
    status (i.e. "connected" or "disconnected"), a CONN-INF message is
    created and dispatched immediately. When the new status is a transitioning
    status (i.e. "connecting" or "disconnecting"), the rate limiter waits for
    at most a given number of seconds (0.1 by default) to see whether the status
    of the connection settles to a stable state ("connected" or "disconnected").
    If the status did not settle, a CONN-INF message is sent with the
    transitioning state. If the status settled to a stable state and it is the
    same as the previous stable state, _no_ message will be sent; otherwise a
    message with the new stable state will be sent.
    """

    @dataclass
    class Entry:
        last_stable_state: ConnectionState
        last_stable_state_timestamp: float = field(default_factory=monotonic)
        settled: Event = field(default_factory=Event)

        @property
        def is_last_stable_state_fresh(self) -> bool:
            return (monotonic() - self.last_stable_state_timestamp) < 0.2

        def notify_settled(self):
            self.settled.set()

        def set_stable_state(self, state):
            self.last_stable_state = state
            self.last_stable_state_timestamp = monotonic()

        async def wait_to_see_if_settles(self, dispatcher):
            with move_on_after(0.1):
                await self.settled.wait()

            if not self.settled.is_set():
                # State of connection did not settle, dispatch a message on
                # our own
                await dispatcher()

    def __init__(self, factory: Callable[[Iterable[str]], FlockwaveMessage]):
        self._factory = factory
        self._request_tx_queue, self._request_rx_queue = open_memory_channel(256)

    def add_request(
        self, uav_id: str, old_state: ConnectionState, new_state: ConnectionState
    ) -> None:
        try:
            self._request_tx_queue.send_nowait((uav_id, old_state, new_state))
        except BrokenResourceError:
            # Message hub is shutting down, this is okay.
            pass

    async def run(self, dispatcher, nursery):
        data = {}

        dispatch_tx_queue, dispatch_rx_queue = open_memory_channel(0)

        async def dispatcher_task():
            async with dispatch_rx_queue:
                async for connection_id in dispatch_rx_queue:
                    data.pop(connection_id, None)
                    try:
                        await dispatcher(self._factory((connection_id,)))
                    except Exception:
                        log.exception(
                            f"Error while dispatching messages from {self.__class__.__name__}"
                        )

        nursery.start_soon(dispatcher_task)

        async with dispatch_tx_queue, self._request_rx_queue:
            async for connection_id, old_state, new_state in self._request_rx_queue:
                if new_state.is_transitioning:
                    # New state shows that the connection is currently transitioning
                    if old_state.is_transitioning:
                        # This is weird; we'd better report the new state straight
                        # away
                        send = True
                    else:
                        # Old state is stable; wait to see whether the new state
                        # stabilizes soon
                        send = False
                        if connection_id not in data:
                            data[connection_id] = entry = self.Entry(old_state)
                            nursery.start_soon(
                                entry.wait_to_see_if_settles,
                                partial(dispatch_tx_queue.send, connection_id),
                            )
                else:
                    # New state is stable; this should be reported
                    send = True
                    entry = data.get(connection_id)
                    if entry:
                        # Let the background task know that we have reached a
                        # stable state so no need to wait further
                        entry.notify_settled()
                        if (
                            entry.last_stable_state == new_state
                            and entry.is_last_stable_state_fresh
                        ):
                            # Stable state is the same as the one we have started
                            # from so no need to send a message
                            send = False
                        else:
                            entry.set_stable_state(new_state)

                if send:
                    await dispatch_tx_queue.send(connection_id)


class RateLimiters:
    """Helper object for managing the dispatch of rate-limited messages.

    This object holds a reference to a dispatcher function that can be used to
    send messages from the message hub of the application. It also holds a
    mapping from _message group names_ to _rate limiters_.

    There are two operations that you can do with this helper object: you
    can either register a new message group name with a corresponding rate
    limiter, or send a request to one of the registered rate limiters, given
    the corresponding message group name. The request contains additional
    positional and keyword arguments that are interpreted by the rate limiter
    and that the rate limiter uses to decide whether to send a message, and if
    so, _when_ to send it.

    For instance, a default rate limiter registered to UAV-INF messages may
    check whether an UAV-INF message has been sent recently, and if so, it
    may wait up to a given number of milliseconds before it sends another
    UAV-INF message, collecting UAV IDs in a temporary variable while it is
    waiting for the next opportunity to send a message. Another rate limiter
    registered to CONN-INF messages may check the new state of a connection,
    and if the new state is a "transitioning" state, it may wait a short
    period of time before sending a message to see whether the transition
    settles or not.

    Rate limiters must satisfy the RateLimiter_ interface. A UAVMessageRateLimiter_
    is provided for the most common use-case where UAV IDs are collected and
    sent in a single batch with a minimum prescribed delay between batches.
    """

    def __init__(self, dispatcher: Callable[[FlockwaveMessage], Awaitable[Any]]):
        """Constructor.

        Parameters:
            dispatcher: the dispatcher function that the rate limiter will use
        """
        self._dispatcher = dispatcher
        self._rate_limiters = {}
        self._running = False

    def register(self, name: str, rate_limiter: RateLimiter) -> None:
        """Registers a new rate limiter corresponding to the message group with
        the given name.

        Parameters:
            name: name of the message group; will be used in `request_to_send()`
                to refer to the rate limiter object associated with this
                group
            rate_limiter: the rate limiter object itself
        """
        if self._running:
            raise RuntimeError(
                "you may not add new rate limiters when the rate limiting tasks "
                "are running"
            )

        self._rate_limiters[name] = rate_limiter

        if hasattr(rate_limiter, "name"):
            rate_limiter.name = name

    def request_to_send(self, name: str, *args, **kwds) -> None:
        """Requests the rate limiter registered with the given name to send
        some messages as soon as the rate limiting rules allow it.

        Additional positional and keyword arguments are forwarded to the
        `add_request()` method of the rate limiter object.

        Parameters:
            name: name of the message group whose rate limiter we are targeting
        """
        self._rate_limiters[name].add_request(*args, **kwds)

    async def run(self):
        self._running = True
        try:
            async with open_nursery() as nursery:
                for entry in self._rate_limiters.values():
                    nursery.start_soon(
                        entry.run,
                        self._dispatcher,
                        nursery,
                        name=f"rate_limiter:{entry.name}/run",
                    )
        finally:
            self._running = False
