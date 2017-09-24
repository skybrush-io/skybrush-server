"""Flockwave message model classes."""

from __future__ import absolute_import

from flockwave.spec.schema import get_message_schema
from future.utils import with_metaclass

from .metamagic import ModelMeta


__all__ = ("FlockwaveMessage", "FlockwaveNotification", "FlockwaveResponse")


class FlockwaveMessage(with_metaclass(ModelMeta, object)):
    """Class representing a single Flockwave message."""

    class __meta__:
        schema = get_message_schema()


class FlockwaveNotification(FlockwaveMessage):
    """Class representing a single Flockwave notification."""

    pass


class FlockwaveResponse(FlockwaveMessage):
    """Specialized Flockwave message that represents a response to some
    other message.
    """

    def add_failure(self, failed_id, reason=None):
        """Adds a failure notification to the response body.

        A common pattern in the Flockwave protocol is that a request
        (such as UAV-INF or CONN-INF) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs (for instance,
        UAV status information is retrieved for all the UAV IDs). When
        one of these requests fail, we do not want to send an error message
        back to the client because the same request could have succeeded for
        *other* IDs. The Flockwave protocol specifies that for such
        messages, the response is allowed to hold a ``failure`` key (whose
        value is a list of failed IDs) and an optional ``reasons`` object
        (which maps failed IDs to textual descriptions of why the operation
        failed). This function handles these two keys in a message.

        When this function is invoked, the given ID will be added to
        the ``failure`` key of the message. The key will be created if it
        does not exist, and the function also checks whether the ID is
        already present in the ``failure`` key or not to ensure that the
        values for the ``failure`` key are unique. When the optional
        ``reason`` argument of this function is not ``None``, the given
        reason is also added to the ``reasons`` key of the message.

        Parameters:
            failed_id (str): the ID for which we want to add a failure
                notification
            reason (str or None): reason for the failure or ``None`` if not
                known or not provided.
        """
        body = self.body
        failures = body.setdefault("failure", [])
        if failed_id not in failures:
            failures.append(failed_id)
        if reason is not None:
            reasons = body.setdefault("reasons", {})
            if failed_id not in reasons:
                reasons[failed_id] = reason

    def add_receipt(self, id, receipt):
        """Adds a receipt for an asynchronous operation to the response
        body.

        A common pattern in the Flockwave protocol is that a request
        (such as CMD-REQ) is able to target multiple identifiers
        (e.g., UAV identifiers or connection identifiers). The request is
        then executed independently for the different IDs, and some of these
        requests may actually trigger the execution of an asynchronous
        command. When the server does not wait with the response until
        the asynchronous command completes, it may choose to create a
        CommandExecutionStatus_ object to track the execution of the command
        and then return the ID of this obejct in the response for a specific
        targeted ID. Clients will then be able to retrieve the status of
        the execution of this command with a CMD-STATUS message, and they
        will also be notified about the completion of these commands in
        separate notifications from the server (the type of which will
        depend on the type of the original message that triggered the
        asynchronous command; for instance, CMD-REQ messages will be
        accompanied with CMD-RESP notifications).

        When this function is invoked, the given ID will be added to
        the ``receipts`` key of the message and it will be associated to
        the ID of the given receipt. The key will be created if it
        does not exist, and the function also checks whether the ID is
        already present in the ``receipts`` key or not to ensure that keys
        are not duplicated.

        Parameters:
            id (str): the ID for which we want to add a receipt
            receipt (CommandExecutionStatus): the execution status object
                whose ID we will use as a receipt to return to the client
        """
        body = self.body
        receipts = body.setdefault("receipts", {})
        receipts[id] = receipt.id

    def add_success(self, successful_id):
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
            successful_id (str): the ID for which we want to add a success
                notification
        """
        body = self.body
        successes = body.setdefault("success", [])
        if successful_id not in successes:
            successes.append(successful_id)
