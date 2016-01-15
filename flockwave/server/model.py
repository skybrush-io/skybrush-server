"""Model classes for Flockwave messages in the server."""

import copy
import jsonschema
import warlock
import warlock.exceptions
import warlock.model

from flockwave.spec.schema import get_message_schema, ref_resolver
from uuid import uuid4


__all__ = ("FlockwaveMessage", "FlockwaveMessageBuilder")


class _Model(warlock.model.Model):
    """Base class for model objects based on a JSON schema."""

    def validate(self, obj):
        try:
            jsonschema.validate(
                obj, self.schema,
                resolver=getattr(self, "resolver", None)
            )
        except jsonschema.ValidationError as exc:
            raise warlock.exceptions.ValidationError(str(exc))


def model_factory(schema, base_class=_Model, name=None):
    """Creates a model class from the given JSON schema.

    Arguments:
        schema (object): the JSON schema of the class to create
        base_class (cls): the base model class
        name (str or None): the name of the class to create

    Returns:
        callable: a new model class with the given schema and name
    """
    schema = copy.deepcopy(schema)
    resolver = jsonschema.RefResolver.from_schema(
        schema, handlers={"http": ref_resolver}
    )

    class Model(base_class):
        def __init__(self, *args, **kwargs):
            self.__dict__['resolver'] = resolver
            self.__dict__['schema'] = schema
            base_class.__init__(self, *args, **kwargs)

    if name is not None:
        Model.__name__ = name
    elif 'name' in schema:
        Model.__name__ = str(schema['name'])

    return Model


FlockwaveMessage = model_factory(
    get_message_schema(), name="FlockwaveMessage")


class FlockwaveMessageBuilder(object):
    """Builder object that can be used to create new Flockwave messages."""

    def __init__(self, version="1.0", id_generator=uuid4,
                 message_factory=FlockwaveMessage):
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
        return FlockwaveMessage(result)

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
        return FlockwaveResponse(result)


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

        When this function is invoked with, the given ID will be added to
        the ``failure`` key of the message. The key will be created if it
        does not exist, and the function also checks whether the ID is
        already present in the ``failure`` key or not to ensure that the
        values for the ``failure`` key are unique. When the optional
        ``reason`` argument of this function is not ``None``, the given
        reason is also added to the ``reasons`` key of the message.

        Parameters:
            body (object): the body of a Flockwave response
            failed_id (str): the ID for which we want to add a failure
                notification
            reason (str or None): reason for the failure or ``None`` if not
                known or not provided.
        """
        body = self.body
        if "failure" not in body:
            failures = body["failure"] = []
        else:
            failures = body["failure"]
        if failed_id not in failures:
            failures.append(failed_id)
        if reason is not None:
            if "reasons" not in body:
                reasons = body["reasons"] = {}
            else:
                reasons = body["reasons"]
            if failed_id not in reasons:
                reasons[failed_id] = reason
