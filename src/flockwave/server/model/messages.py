"""Flockwave message model classes."""

from flockwave.spec.schema import get_message_schema
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .commands import CommandExecutionStatus
from .metamagic import ModelMeta


__all__ = ("FlockwaveMessage", "FlockwaveNotification", "FlockwaveResponse")


class FlockwaveMessage(metaclass=ModelMeta):
    """Class representing a single Flockwave message, irrespectively of whether
    it is a request, a notification or a response.
    """

    id: str
    body: Dict[str, Any]

    class __meta__:
        schema = get_message_schema()

    def get_ids(self) -> Sequence[str]:
        """Returns the `"ids"` property of the message body, or an empty sequence
        if there is no such member in the body.
        """
        return self.body.get("ids") or ()

    def get_type(self) -> str:
        """Returns the `"type"` property of the message body, or an empty string
        if there is no such member in the body or if there is no message body.
        """
        if hasattr(self, "body"):
            return str(self.body.get("type", ""))
        else:
            return ""

    @staticmethod
    def is_experimental(message: dict) -> bool:
        """Returns whether the given raw JSON representation of a Flockwave
        message contains an experimental message type for which no validation
        schema exists.
        """
        body = message.get("body")
        type = body.get("type") if isinstance(body, dict) else None
        return isinstance(type, str) and type.startswith("X-")


class FlockwaveNotification(FlockwaveMessage):
    """Class representing a single Flockwave notification."""

    pass


class FlockwaveResponse(FlockwaveMessage):
    """Specialized Flockwave message that represents a response to some
    other message.
    """

    refs: List[str]

    def __init__(self, *args, **kwds):
        self._on_sent = []
        super().__init__(*args, **kwds)

    def add_error(self, failed_id: str, reason: Optional[Union[str, Exception]] = None):
        """Adds an error message to the response body.

        A common pattern in the Flockwave protocol is that a request
        (such as UAV-INF or CONN-INF) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs (for instance,
        UAV status information is retrieved for all the UAV IDs). When
        one of these requests fail, we do not want to send an error message
        back to the client because the same request could have succeeded for
        *other* IDs. The Flockwave protocol specifies that for such
        messages, the response is allowed to hold an ``error`` key (whose
        value is a mapping from failed IDs to the corresponding error messages).
        This function handles the ``error`` key in such messages.

        When this function is invoked, the given ID will be added to
        the ``error`` mapping of the message. The key will be created if it
        does not exist. When the optional ``reason`` argument of this function
        is not ``None``, the given reason is added to the error mapping;
        otherwise an empty string is added to the ``error`` key of the message.

        Parameters:
            failed_id (str): the ID for which we want to add a failure
                notification
            reason (str or None): reason for the failure or ``None`` if not
                known or not provided.
        """
        body = self.body
        errors = body.setdefault("error", {})
        errors[failed_id] = str(reason or "")

    def add_receipt(self, id: str, receipt: CommandExecutionStatus):
        """Adds a receipt for an asynchronous operation to the response
        body.

        A common pattern in the Flockwave protocol is that a request
        (such as OBJ-CMD) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs, and some of these
        requests may actually trigger the execution of an asynchronous
        command. When the server does not wait with the response until
        the asynchronous command completes, it may choose to create a
        CommandExecutionStatus_ object to track the execution of the command
        and then return the ID of this obejct in the response for a specific
        targeted ID. Clients will then be notified about the completion of these
        commands in separate ASYNC-RESP notifications from the server.

        When this function is invoked, the given ID will be added to
        the ``receipts`` key of the message and it will be associated to
        the ID of the given receipt. The key will be created if it
        does not exist, and the function also checks whether the ID is
        already present in the ``receipts`` key or not to ensure that keys
        are not duplicated.

        Parameters:
            id: the ID for which we want to add a receipt
            receipt: the execution status object whose ID we will use as a
                receipt to return to the client
        """
        body = self.body
        receipts = body.setdefault("receipt", {})
        receipts[id] = receipt.id

    def add_result(self, id: str, value: Any = None) -> None:
        """Adds a result object to the response body.

        A common pattern in the Flockwave protocol is that a request
        (such as UAV-INF or CONN-INF) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs (for instance,
        UAV status information is retrieved for all the UAV IDs). For
        successful executions, the result of the execution should be added to
        a dictionary mapping IDs to the results.

        When this function is invoked, the given ID will be added to
        the ``result`` mapping of the message. mapping key will be created if it
        does not exist.

        Parameters:
            successful_id: the ID for which we want to add a success
                notification
            value: the result object to associate to the given ID
        """
        body = self.body
        results = body.setdefault("result", {})
        results[id] = value

    def add_success(self, successful_id: str) -> None:
        """Adds a success notification to the response body.

        A common pattern in the Flockwave protocol is that a request
        (such as UAV-INF or CONN-INF) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs (for instance,
        UAV status information is retrieved for all the UAV IDs). For
        successful executions, the ID of the target should then be added to
        a ``success`` key.

        When this function is invoked, the given ID will be added to
        the ``success`` key of the message. The key will be created if it
        does not exist, and the function also checks whether the ID is
        already present in the ``success`` key or not to ensure that keys
        are not duplicated.

        Parameters:
            successful_id: the ID for which we want to add a success
                notification
        """
        body = self.body
        successes = body.setdefault("success", [])
        if successful_id not in successes:
            successes.append(successful_id)

    def receipts(self) -> Iterable[str]:
        """Iterates over all receipt IDs that are found in the body of the
        message.
        """
        receipts = self.body.get("receipt")
        if isinstance(receipts, dict):
            yield from (receipt_id for receipt_id in receipts.values())

    def when_sent(self, func, *args, **kwds):
        """Registers a function to be called when the message is sent."""
        self._on_sent.append((func, args, kwds))

    def _notify_sent(self):
        """Notifies the message that it was successfully sent to all the
        clients it should have been sent to. Calls all registered handlers
        in a synchronous manner.
        """
        for func, args, kwds in self._on_sent:
            func(*args, **kwds)
