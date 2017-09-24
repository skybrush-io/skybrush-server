"""Classes related to message handling in Flockwave."""

from __future__ import absolute_import

from collections import defaultdict
from future.utils import string_types
from itertools import chain
from jsonschema import ValidationError

from .logger import log as base_log
from .model import Client, FlockwaveMessage, FlockwaveMessageBuilder, \
    FlockwaveNotification, FlockwaveResponse
from .registries import ClientRegistry

__all__ = ("MessageHub", )

log = base_log.getChild("message_hub")


class MessageValidationError(RuntimeError):
    """Error that is thrown by the MessageHub_ class internally when it
    fails to validate an incoming message against the Flockwave schema.
    """

    pass


class MessageHub(object):
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

    def __init__(self):
        """Constructor."""
        self._handlers_by_type = defaultdict(list)
        self._message_builder = FlockwaveMessageBuilder()
        self._broadcast_methods = None
        self._channel_type_registry = None
        self._client_registry = None

    def acknowledge(self, message=None, outcome=True, reason=None):
        """Creates a new positive or negative acknowledgment of the given
        message.

        Parameters:
            message (FlockwaveMessage): the message to respond to with a
                positive or negative acknowledgmnt
            outcome (bool): ``True`` to construct a positive acknowledgment,
                ``False`` otherwise.
            reason (Optional[str]): the reason for a negative acknowledgment;
                ignored for positive acknowledgments

        Returns:
            FlockwaveResponse: a response object that acknowledges the
                given message
        """
        body = {
            "type": "ACK-ACK" if outcome else "ACK-NAK",
        }
        if not outcome and reason:
            body["reason"] = reason
        return self._message_builder.create_response_to(message, body)

    def broadcast_message(self, message):
        """Sends a broadcast message from this message hub.

        Parameters:
            message (FlockwaveNotification): the notification to broadcast.
        """
        assert isinstance(message, FlockwaveNotification), \
            "only notifications may be broadcast"
        assert isinstance(self.client_registry, ClientRegistry), \
            "message hub does not have a client registry yet"

        if self._broadcast_methods is None:
            self._broadcast_methods = self._commit_broadcast_methods()

        if not self._broadcast_methods:
            return

        if message.body["type"] not in ("UAV-INF", "DEV-INF"):
            log.info(
                "Broadcasting {0.body[type]} notification".format(message),
                extra={
                    "id": message.id,
                    "semantics": "notification"
                }
            )

        for func, args in self._broadcast_methods:
            func(message, *args)

    @property
    def channel_type_registry(self):
        """Registry that keeps track of the different channel types that the
        app can handle. This is used by the message hub to figure out how to
        broadcast messages to all connected clients.
        """
        return self._channel_type_registry

    @channel_type_registry.setter
    def channel_type_registry(self, value):
        if self._channel_type_registry == value:
            return

        if self._channel_type_registry is not None:
            self._channel_type_registry.added.disconnect(
                self._invalidate_broadcast_methods,
                sender=self._channel_type_registry
            )
            self._channel_type_registry.removed.disconnect(
                self._invalidate_broadcast_methods,
                sender=self._channel_type_registry
            )

        self._channel_type_registry = value

        if self._channel_type_registry is not None:
            self._channel_type_registry.added.connect(
                self._invalidate_broadcast_methods,
                sender=self._channel_type_registry
            )
            self._channel_type_registry.removed.connect(
                self._invalidate_broadcast_methods,
                sender=self._channel_type_registry
            )

    @property
    def client_registry(self):
        """Registry that keeps track of connected clients so the message hub
        can broadcast messages to all connected clients.
        """
        return self._client_registry

    @client_registry.setter
    def client_registry(self, value):
        if self._client_registry == value:
            return

        if self._client_registry is not None:
            self._client_registry.added.disconnect(
                self._invalidate_broadcast_methods,
                sender=self._client_registry
            )
            self._client_registry.removed.disconnect(
                self._invalidate_broadcast_methods,
                sender=self._client_registry
            )

        self._client_registry = value

        if self._client_registry is not None:
            self._client_registry.added.connect(
                self._invalidate_broadcast_methods,
                sender=self._client_registry
            )
            self._client_registry.removed.connect(
                self._invalidate_broadcast_methods,
                sender=self._client_registry
            )

    def create_notification(self, body=None):
        """Creates a new Flockwave notification to be sent by the server.

        Parameters:
            body (object): the body of the notification.

        Returns:
            FlockwaveNotification: a notification object
        """
        return self._message_builder.create_notification(body)

    def create_response_or_notification(self, body=None, in_response_to=None):
        """Creates a new Flockwave response or notification object,
        depending on whether the caller specifies a message to respond to
        or not.

        Parameters:
            body (object): the body of the response.
            message (FlockwaveMessage or None): the message to respond to
                or ``None`` if we want to createa a notification instead.

        Returns:
            FlockwaveMessage: a response or notification object
        """
        if in_response_to is None:
            return self.create_notification(body)
        else:
            return self.create_response_to(in_response_to, body)

    def create_response_to(self, message, body=None):
        """Creates a new Flockwave response object that will respond to the
        given message.

        Parameters:
            message (FlockwaveMessage): the message to respond to
            body (object): the body of the response.

        Returns:
            FlockwaveResponse: a response object that will respond to the
                given message
        """
        return self._message_builder.create_response_to(message, body)

    def handle_incoming_message(self, message, sender):
        """Handles an incoming Flockwave message by calling the appropriate
        message handlers.

        Parameters:
            message (dict): the incoming message, already decoded from
                its string representation into a Python dict, but before
                it was validated against the Flockwave schema
            sender (Client): the sender of the message

        Returns:
            bool: whether the message was handled by at least one handler
                or internally by the hub itself
        """
        try:
            message = self._decode_incoming_message(message)
        except MessageValidationError as ex:
            reason = ex.message
            log.exception(reason)
            if u"id" in message:
                ack = self.acknowledge(message, outcome=False,
                                       reason=reason)
                self.send_message(ack, to=sender)
                return True

        if "error" in message:
            log.warning("Error message from Flockwave client silently dropped")
            return True

        log.info(
            "Received {0.body[type]} message".format(message),
            extra={
                "id": message.id,
                "semantics": "request"
            }
        )

        if not self._feed_message_to_handlers(message, sender):
            log.warning(
                "Unhandled message: {0.body[type]}".format(message),
                extra={
                    "id": message.id
                }
            )
            ack = self.acknowledge(message, outcome=False,
                                   reason="No handler managed to parse this "
                                          "message in the server")
            self.send_message(ack, to=sender)
            return False

        return True

    def _commit_broadcast_methods(self):
        """Calculates the list of methods to call when the message hub
        wishes to broadcast a message to all the connected clients.
        """
        result = []
        clients_for = self._client_registry.client_ids_for_channel_type
        has_clients_for = self._client_registry.has_clients_for_channel_type

        for channel_type_id in self._channel_type_registry.ids:
            descriptor = self._channel_type_registry[channel_type_id]
            broadcaster = descriptor.broadcaster
            if broadcaster:
                if has_clients_for(descriptor.id):
                    result.append((broadcaster, []))
            else:
                clients = clients_for(descriptor.id)
                for client_id in clients:
                    result.append((self._send_message, [client_id]))

        return result

    def _feed_message_to_handlers(self, message, sender):
        """Forwards an incoming, validated Flockwave message to the message
        handlers registered in the message hub.

        Parameters:
            message (FlockwaveMessage): the message to process
            sender (Client): the sender of the message

        Returns:
            bool: whether the message was handled by at least one handler
        """
        message_type = message.body["type"]
        all_handlers = chain(
            self._handlers_by_type.get(message_type, ()),
            self._handlers_by_type[None]
        )

        handled = False
        for handler in all_handlers:
            try:
                response = handler(message, sender, self)
            except Exception:
                log.exception("Error while calling handler {0!r} "
                              "for incoming message; proceeding with "
                              "next handler (if any)".format(handler))
                response = None
            if response is True:
                # Message was handled by the handler
                handled = True
            elif response is False or response is None:
                # Message was rejected by the handler, nothing to do
                pass
            elif isinstance(response, (dict, FlockwaveResponse)):
                # Handler returned a dict or a response; we must send it
                self._send_response(response, to=sender,
                                    in_response_to=message)
                handled = True

        return handled

    def _invalidate_broadcast_methods(self, *args, **kwds):
        """Invalidates the list of methods to call when the message hub
        wishes to broadcast a message to all the connected clients.
        """
        self._broadcast_methods = None

    def on(self, *args):
        """Decorator factory function that allows one to register a message
        handler on a MessageHub_ with the following syntax::

            @message_hub.on("SYS-VER")
            def handle_SYS_VER(message, sender, hub):
                [...]
        """
        def decorator(func):
            self.register_message_handler(func, args)
            return func
        return decorator

    def register_message_handler(self, func, message_types=None):
        """Registers a handler function that will handle incoming messages.

        It is possible to register the same handler function multiple times,
        even for the same message type.

        Parameters:
            func (callable): the handler to register. It will be called with
                the incoming message and the message hub object.

            message_types (None or iterable): an iterable that yields the
                message types for which this handler will be registered.
                ``None`` means to register the handler for all message
                types. The handler function must return ``True`` if it has
                handled the message successfully, ``False`` if it skipped
                the message. Note that returning ``True`` will not prevent
                other handlers from getting the message.
        """
        if message_types is None or isinstance(message_types, string_types):
            message_types = [message_types]

        for message_type in message_types:
            if not isinstance(message_type, str):
                message_type = message_type.decode("utf-8")
            self._handlers_by_type[message_type].append(func)

    def send_message(self, message, to=None, in_response_to=None):
        """Sends a message or notification from this message hub.

        Notifications are sent to all connected clients, unless ``to`` is
        specified, in which case they are sent only to the given client.

        Messages are sent only to the client whose request is currently
        being served, unless ``to`` is specified, in which case they are
        sent only to the given client.

        Parameters:
            message (FlockwaveMessage): the message to send.
            to (Optional[Union[str, Client]]): the Client_ object that
                represents the recipient of the message, or the ID of the
                client. ``None`` means to send the message to all connected
                clients.
            in_response_to (Optional[FlockwaveMessage]): the message that
                the message being sent responds to.
        """
        if to is None:
            assert in_response_to is None, "broadcast messages cannot be "\
                "sent in response to a particular message"
            return self.broadcast_message(message)

        if in_response_to is not None:
            log.info(
                "Sending {0.body[type]} response".format(message),
                extra={
                    "id": in_response_to.id,
                    "semantics": "response_success"
                }
            )
        elif isinstance(message, FlockwaveNotification):
            if message.body["type"] not in ("UAV-INF", "DEV-INF"):
                log.info(
                    "Sending {0.body[type]} notification".format(message),
                    extra={
                        "id": message.id,
                        "semantics": "notification"
                    }
                )
        else:
            log.info(
                "Sending {0.body[type]} message".format(message),
                extra={
                    "id": message.id,
                    "semantics": "response_success"
                }
            )

        self._send_message(message, to)

    def _decode_incoming_message(self, message):
        """Decodes an incoming, raw JSON message that has already been
        decoded from the string representation into a dictionary on the
        Python side, but that has not been validated against the Flockwave
        message schema.

        Parameters:
            message (dict): the incoming, raw message

        Returns:
            message (FlockwaveMessage): the validated message as a Python
                FlockwaveMessage_ object

        Raises:
            MessageDecodingError: if the message could not have been decoded
        """
        try:
            return FlockwaveMessage.from_json(message)
        except ValidationError:
            raise MessageValidationError(
                "Flockwave message does not match schema"
            )
        except Exception as ex:
            raise MessageValidationError(
                "Unexpected exception: {0!r}".format(ex)
            )

    def _send_message(self, message, client_or_id):
        if not isinstance(client_or_id, Client):
            try:
                client = self._client_registry[client_or_id]
            except KeyError:
                log.warn("Client {0!r} is gone; not sending message".format(
                    client_or_id
                ))
        else:
            client = client_or_id
        client.channel.send(message)

    def _send_response(self, message, to, in_response_to):
        """Sends a response to a message from this message hub.

        Parameters:
            message (FlockwaveResponse or object): the response, or the body
                of the response. When it is a FlockwaveResponse_ object, the
                function will check whether the response indeed refers to
                the given message (in the ``in_response_to`` parameter).
                When it is any other object, it will be wrapped in a
                FlockwaveResponse_ object first. In both cases, the type
                of the message body will be filled from the type of the
                original message if it is not given.
            to (Client):  a Client_ object that represents the intended
                recipient of the message.
            in_response_to (FlockwaveMessage): the message that the given
                object is responding to

        Returns:
            FlockwaveResponse: the response that was sent back to the client
        """
        if isinstance(message, FlockwaveResponse):
            assert message.correlationId == in_response_to.id
            response = message
        else:
            try:
                response = self._message_builder.create_response_to(
                    in_response_to, body=message
                )
            except Exception:
                log.exception("Failed to create response")
        self.send_message(response, to=to, in_response_to=in_response_to)
        return response
