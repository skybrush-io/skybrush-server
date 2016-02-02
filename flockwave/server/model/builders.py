"""Builder classes for model objects for sake of convenience."""

from __future__ import absolute_import

from uuid import uuid4

from .messages import FlockwaveMessage, FlockwaveNotification, \
    FlockwaveResponse

__all__ = ("FlockwaveMessageBuilder", )


class FlockwaveMessageBuilder(object):
    """Builder object that can be used to create new Flockwave messages."""

    def __init__(self, version="1.0", id_generator=uuid4):
        """Constructs a new message builder.

        Parameters:
            version (string): the version of the Flockwave protocol that
                we put in the generated messages
            id_generator (callable): callable that will generate a new
                message ID when called without arguments
        """
        self.id_generator = id_generator
        self.version = version

    def create_message(self, body=None):
        """Creates a new Flockwave message with the given body.

        Parameters:
            body (object): the body of the message.

        Returns:
            FlockwaveMessage: the newly created message
        """
        result = {
            "$fw.version": self.version,
            "id": unicode(self.id_generator()),
            "body": body
        }
        return FlockwaveMessage.from_json(result, validate=False)

    def create_notification(self, body=None):
        """Creates a new Flockwave notification with the given body.

        Parameters:
            body (object): the body of the notification.

        Returns:
            FlockwaveNotification: the newly created notification
        """
        result = {
            "$fw.version": self.version,
            "id": unicode(self.id_generator()),
            "body": body
        }
        return FlockwaveNotification.from_json(result, validate=False)

    def create_response_to(self, message, body=None):
        """Creates a new Flockwave message that is a response to the
        given message.

        Parameters:
            message (FlockwaveMessage): the message that the constructed
                message will respond to
            body (object): the body of the response. When it is not ``None``
                and its type is missing, the type will be made equal to the
                type of the incoming message.

        Returns:
            FlockwaveMessage: the newly created response
        """
        if body is not None and "type" not in body:
            body["type"] = message.body["type"]

        result = {
            "$fw.version": self.version,
            "id": unicode(self.id_generator()),
            "correlationId": message.id,
            "body": body
        }
        return FlockwaveResponse.from_json(result, validate=False)
