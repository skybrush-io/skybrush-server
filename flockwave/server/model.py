"""Model classes for Flockwave messages in the server."""

import jsonschema

from flockwave.spec.schema import get_message_schema, ref_resolver
from uuid import uuid4


__all__ = (
    "FlockwaveMessage", "FlockwaveMessageBuilder",
    "FlockwaveResponse"
)


class ModelMeta(type):
    """Metaclass for our model objects. Adds JSON validation automatically
    when the model objects are constructed from JSON.
    """

    def __new__(cls, clsname, bases, dct):
        bases_have_schema = any(
            getattr(base, "__metaclass__", type) is ModelMeta
            for base in bases
        )
        schema, resolver = cls.find_schema_and_resolver(dct, bases)
        if schema is not None:
            if not bases_have_schema:
                cls.add_json_property(dct)
                cls.add_special_methods(dct)
            cls.add_validator_method(dct, schema, resolver)
        return type.__new__(cls, clsname, bases, dct)

    @staticmethod
    def add_json_property(dct):
        """Extends the class being constructed with a ``json`` property
        that contains the instance data in JSON format. Setting the property
        will trigger a full JSON schema validation.
        """
        orig_init = dct.get("__init__", None)

        def __init__(self, json=None, *args, **kwds):
            if orig_init is not None:
                orig_init(self, *args, **kwds)
            self.__dict__["_json"] = {}
            self.json = json

        if orig_init and hasattr(orig_init, "__doc__"):
            __init__.__doc__ = orig_init.__doc__

        def getjson(self):
            """The value of the object in JSON format"""
            return self._json
        def setjson(self, value):
            if self._json is value:
                return
            self._json = value
            self.validate()

        @classmethod
        def from_json(cls, data):
            """Constructs this model object from its JSON representation."""
            return cls(json=data)

        dct.update(
            __init__=__init__,
            from_json=from_json,
            json=property(getjson, setjson, doc=getjson.__doc__)
        )

    @staticmethod
    def add_special_methods(dct):
        """Adds some special methods to the class dictionary that allows
        attributes of the wrapped JSON object to be accessed with member
        and dictionary notation.
        """
        # TODO: don't use this hackery; add properties instead
        def __contains__(self, key):
            return key in self._json
        def __getattr__(self, key):
            try:
                return self.__getitem__(key)
            except KeyError:
                raise AttributeError(key)
        def __getitem__(self, key):
            return self._json[key]
        for name in ["__contains__", "__getattr__", "__getitem__"]:
            if name not in dct:
                dct[name] = locals()[name]

    @staticmethod
    def add_validator_method(dct, schema, resolver):
        """Adds a ``validate()`` method to the given class dictionary that
        validates the class instance against a JSON schema.

        If the dictionary already has a ``validate()`` method, the JSON
        schema validation will be performed *before* the original
        ``validate()`` method.

        Parameters:
            dct (dict): the class dictionary
            schema (dict): the JSON schema that the class instances must be
                validated against
            resolver (object): a JSON reference resolver that will be used
                to resolve JSON references. ``None`` means to use the
                default resolver from ``jsonschema``.
        """
        orig_validator = dct.get("validate", None)
        if orig_validator is not None and not callable(orig_validator):
            raise TypeError("validate() method must be callable")

        def _validate_object(obj):
            jsonschema.validate(obj, schema, resolver=resolver)

        def validate(self, *args, **kwds):
            """Validates this class instance against its JSON schema.

            Throws:
                jsonschema.ValidationError: if the class instance does not
                    match its schema
            """
            _validate_object(self._json)
            if orig_validator is not None:
                return orig_validator(*args, **kwds)

        if orig_validator and hasattr(orig_validator, "__doc__"):
            validate.__doc__ = orig_validator.__doc__

        dct["validate"] = validate

    @classmethod
    def find_schema_and_resolver(cls, dct, bases):
        """Finds the JSON schema that the class being constructed must
        adhere to. This is done by looking up the ``schema`` attribute
        in the class dictionary. If no such attribute is found, one of
        the bases must be derived from this metaclass; in such cases,
        we assume that the class being constructed here must adhere to
        the same schema as the base so we simply return ``None``,
        indicating that no additional schema validation is needed.

        Parameters:
            dct (dict): the class dictionary
            bases (list of type): list of the base classes

        Returns:
            (object, object): a pair where the first object is the JSON
                schema of the class to be constructed or ``None`` if the
                class does not need schema validation, and the second
                object is the JSON schema resolver to use or ``None`` if
                the default JSON schema resolver should be used
        """
        if "ref_resolver" in dct:
            resolver = dct.pop("ref_resolver")
        else:
            resolver = None
        if "schema" in dct:
            return dct.pop("schema"), resolver
        if any(getattr(base, "__metaclass__", type) is cls for base in bases):
            return None, resolver
        raise TypeError("Model classes must either have a 'schema' "
                        "attribute or derive from another model class "
                        "with a schema")


class FlockwaveMessage(object):
    """Class representing a single Flockwave message."""

    __metaclass__ = ModelMeta
    schema = get_message_schema()
    ref_resolver = jsonschema.RefResolver.from_schema(
        schema, handlers={"http": ref_resolver}
    )


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
        return FlockwaveMessage.from_json(result)

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
        return FlockwaveResponse.from_json(result)


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
