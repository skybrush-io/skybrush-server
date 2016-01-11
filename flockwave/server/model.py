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

        Arguments:
            version (string): the version of the Flockwave protocol that
                we put in the generated messages
            id_generator (callable): callable that will generate a new
                message ID when called without arguments
            message_factory (callable): callable that generates a new
                Flockwave message object when called with a dictionary
                containing the JSON representation of the message that
                we built.
        """
        self.id_generator = id_generator
        self.message_factory = message_factory
        self.version = version

    def create_message(self, body=None):
        """Creates a new Flockwave message with the given body.

        Arguments:
            body (object): the body of the message.

        Returns:
            FlockwaveMessage: the newly created message
        """
        result = {
            "$fw.version": self.version,
            "id": unicode(self.id_generator()),
            "body": body
        }
        return self.message_factory(result)

    def create_response_to(self, message, body=None):
        """Creates a new Flockwave message that is a response to the
        given message.

        Arguments:
            message (FlockwaveMessage): the message that the constructed
                message will respond to
            body (object): the body of the response.

        Returns:
            FlockwaveMessage: the newly created response
        """
        result = self.create_message(body)
        if message is not None:
            result.correlationId = message.id
        return result
