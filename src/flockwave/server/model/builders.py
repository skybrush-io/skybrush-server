"""Builder classes for model objects for sake of convenience."""

from builtins import str
from typing import Any, Callable, Dict

from .commands import CommandExecutionStatus
from .identifiers import default_id_generator
from .messages import FlockwaveMessage, FlockwaveNotification, FlockwaveResponse

__all__ = ("CommandExecutionStatusBuilder", "FlockwaveMessageBuilder")


class CommandExecutionStatusBuilder:
    """Builder object that can be used to create new command execution
    status objects.
    """

    id_generator: Callable[[], str]

    def __init__(self, id_generator: Callable[[], str] = default_id_generator):
        """Constructs a new command execution status builder.

        Parameters:
            id_generator (callable): callable that will generate a new
                receipt ID for a command execution status object when
                called without arguments
        """
        self.id_generator = id_generator

    def create_status_object(self) -> CommandExecutionStatus:
        """Creates a new command execution status object.

        Returns:
            CommandExecutionStatus: the newly created command execution
                status object
        """
        return CommandExecutionStatus(id=self.id_generator())


class FlockwaveMessageBuilder(object):
    """Builder object that can be used to create new Flockwave messages."""

    id_generator: Callable[[], str]
    version: str

    def __init__(
        self,
        version: str = "1.0",
        id_generator: Callable[[], str] = default_id_generator,
    ):
        """Constructs a new message builder.

        Parameters:
            version: the version of the Flockwave protocol that we put in the
                generated messages
            id_generator: callable that will generate a new message ID when
                called without arguments
        """
        self.id_generator = id_generator
        self.version = version

    def _create_message_object(self, body: Any = None) -> Dict[str, Any]:
        """Creates a new Flockwave message object with the given body.

        Parameters:
            body (Optional[object]): the body of the message.

        Returns:
            FlockwaveMessage: the newly created message
        """
        result = {"$fw.version": self.version, "id": str(self.id_generator())}
        if body is not None:
            result["body"] = body
        return result

    def create_message(self, body: Any = None) -> FlockwaveMessage:
        """Creates a new Flockwave message with the given body.

        Parameters:
            body: the body of the message.

        Returns:
            the newly created message
        """
        result = self._create_message_object(body)
        return FlockwaveMessage.from_json(result, validate=False)  # type: ignore

    def create_notification(self, body: Any = None) -> FlockwaveNotification:
        """Creates a new Flockwave notification with the given body.

        Parameters:
            body: the body of the notification.

        Returns:
            the newly created notification
        """
        result = self._create_message_object(body)
        return FlockwaveNotification.from_json(result, validate=False)  # type: ignore

    def create_response_to(self, message: Any, body: Any = None) -> FlockwaveResponse:
        """Creates a new Flockwave message that is a response to the
        given message.

        Parameters:
            message: the message that the constructed message will respond to
            body (object): the body of the response. When it is not ``None``
                and its type is missing, the type will be made equal to the
                type of the incoming message.

        Returns:
            FlockwaveMessage: the newly created response
        """
        if body is not None and "type" not in body:
            body["type"] = message.body["type"]

        if hasattr(message, "id"):
            message_id = message.id
        else:
            message_id = message["id"]

        result = self._create_message_object(body)
        result["refs"] = message_id
        return FlockwaveResponse.from_json(result, validate=False)  # type: ignore
