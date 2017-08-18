"""Classes related to message handling in Flockwave."""

from __future__ import absolute_import

from collections import defaultdict
from flask_socketio import emit
from future.utils import string_types
from itertools import chain

from .logger import log as base_log
from .model import Client, FlockwaveMessageBuilder, FlockwaveNotification, \
    FlockwaveResponse

__all__ = ("MessageHub", )

log = base_log.getChild("message_hub")


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
        self.socketio = None

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

    def handle_incoming_message(self, message):
        """Handles an incoming Flockwave message by calling the appropriate
        message handlers.

        Parameters:
            message (FlockwaveMessage): the incoming message

        Returns:
            bool: whether the message was handled by at least one handler
        """
        log.info(
            "Received {0.body[type]} message".format(message),
            extra={
                "id": message.id,
                "semantics": "request"
            }
        )

        handled = False
        message_type = message.body["type"]
        all_handlers = chain(
            self._handlers_by_type.get(message_type, ()),
            self._handlers_by_type[None]
        )
        for handler in all_handlers:
            try:
                response = handler(message, self)
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
                self._send_response(response, in_response_to=message)
                handled = True

        return handled

    def on(self, *args):
        """Decorator factory function that allows one to register a message
        handler on a MessageHub_ with the following syntax::

            @message_hub.on("SYS-VER")
            def handle_SYS_VER(message, hub):
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
                the incoming message and the message hub object. The handler
                is guaranteed to be called in a Flask request context.

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
        specified, in which case they are sent only to the given room or
        client.

        Messages are sent only to the client whose request is currently
        being served, unless ``to`` is specified, in which case they are
        sent only to the given room or client.

        Parameters:
            message (FlockwaveMessage): the message to send.
            to (Optional[Union[str, Client]]): room name or session
                identifier for a client where the message should be sent,
                or a Client_ object that will be the recipient of the
                message. ``None`` means to send messages to the client whose
                request is currently being served and send notifications to
                everyone.
            in_response_to (Optional[FlockwaveMessage]): the message that
                the message being sent responds to.
        """
        broadcast = False
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
            broadcast = True
        else:
            log.info(
                "Sending {0.body[type]} message".format(message),
                extra={
                    "id": message.id,
                    "semantics": "response_success"
                }
            )

        if isinstance(to, Client):
            to = to.id

        if to is not None:
            broadcast = False

        if not broadcast and to is None:
            # We are trying to send a message to the sender of the current
            # request. This works only with the Flask-SocketIO-wide
            # emit() function so we use that
            emit("fw", message.json)
        else:
            # We are either sending a broadcast or targeting a concrete
            # client; this can work with our own _socketio object
            assert self.socketio, "message hub is not associated to "\
                "a SocketIO object yet"
            self.socketio.emit(
                "fw", message.json, room=to, namespace="/"
            )

    def _send_response(self, message, in_response_to):
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
        self.send_message(response, in_response_to=in_response_to)
        return response
