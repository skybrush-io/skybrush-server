"""Model classes for Flockwave messages in the server."""

import copy
import jsonschema
import warlock
import warlock.exceptions
import warlock.model

from flockwave.spec.schema import get_message_schema, ref_resolver


__all__ = ("FlockwaveMessage", )


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
