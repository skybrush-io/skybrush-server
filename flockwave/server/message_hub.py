"""Classes related to message handling in Flockwave."""

from __future__ import absolute_import

from collections import defaultdict
from flask.ext.socketio import emit
from itertools import chain

from .logger import log as base_log
from .model import FlockwaveMessageBuilder

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

    Message handler functions are required to return ``False`` or ``None``
    if they decided not to handle the message they were given. Handler
    functions may also return ``True`` to indicate that they have handled
    the message and no further action is needed, or a ``dict`` that contains
    a response body that should be sent back to the caller.
    """

    def __init__(self):
        """Constructor."""
        self._handlers_by_type = defaultdict(list)
        self._message_builder = FlockwaveMessageBuilder()

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
            response = handler(message, self)
            if response or isinstance(response, dict):
                handled = True
                if isinstance(response, dict):
                    self.send_response(message, response)

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
        if message_types is None or isinstance(message_types, basestring):
            message_types = [message_types]

        for message_type in message_types:
            if not isinstance(message_type, unicode):
                message_type = message_type.decode("utf-8")
            self._handlers_by_type[message_type].append(func)

    def send_response(self, message, body):
        """Sends a response to a message from this message hub.

        Arguments:
            message (FlockwaveMessage): the message to respond to
            body (object): the body of the response to the message

        Returns:
            the newly constructed response that was sent back to the client
        """
        if "type" not in body:
            body["type"] = message.body["type"]

        response = self._message_builder.create_response_to(message)
        response.body = body

        log.info(
            "Sending {0.body[type]} response".format(response),
            extra={
                "id": message.id,
                "semantics": "response_success"
            }
        )

        emit("fw", response, json=True)
        return response
