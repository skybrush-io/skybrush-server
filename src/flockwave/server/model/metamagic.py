"""Metaclass (and related things) for the Flockwave object model.

The metaclass defined here allows us to create classes that generate
properties for themselves and validate themselves automatically based on
a JSON schema description.
"""

import jsonschema

from contextlib import contextmanager
from dataclasses import dataclass
from flockwave.spec.schema import ref_resolver as flockwave_schema_ref_resolver
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = ("ModelMeta",)


#: Type specification for a mapper function that converts a property to its
#: JSON representation or vice versa
Mapper = Callable[[Any], Any]

#: Pair of mapper functions, one to convert from JSON and the other one to
#: convert to JSON
MapperPair = Tuple[Mapper, Mapper]


@dataclass
class PropertyInfo:
    """Simple tuple subclass to hold information about a single property
    in a JSON schema.
    """

    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    default: Any = None
    mappers: Optional[MapperPair] = None

    @classmethod
    def from_json_schema(cls, name: str, definition: Dict):
        """Constructs a property information object from its JSON schema
        representation.

        Parameters:
            name: the name of the property that appears as a key in a
                ``properties`` stanza of a JSON schema object
            definition: the JSON schema definition of the property

        Returns:
            PropertyInfo: the property information object
        """
        return PropertyInfo(
            name=name,
            title=definition.get("title"),
            description=definition.get("description"),
            default=definition.get("default"),
        )


def collect_properties(schema, resolver, mappers, result=None):
    """Collects information about all the properties defined on a JSON
    schema.

    Parameters:
        schema (object): the JSON schema
        resolver (jsonschema.RefResolver): reference resolver for the
            JSON schema
        mappers (dict): dictionary that maps property names to pairs of
            converter functions to be used when deserializing and serializing
            the property
        result (dict or None): dictionary to extend with the property
            information. ``None`` means to construct and return a new
            dictionary.

    Returns:
        dict: dictionary mapping property names to PropertyInfo_ objects.
            Identical to the ``result`` parameter if it was a dict.
    """
    if result is None:
        result = {}

    # Handle '$ref' keyword
    if "$ref" in schema:
        with resolver.resolving(schema["$ref"]) as subschema:
            return collect_properties(subschema, resolver, mappers, result)

    # Handle 'allOf' keyword
    if "allOf" in schema:
        for subschema in schema["allOf"]:
            collect_properties(subschema, resolver, mappers, result)
        return result

    # Handle 'anyOf' keyword
    if "anyOf" in schema:
        for subschema in schema["anyOf"]:
            collect_properties(subschema, resolver, mappers, result)
        return result

    # Handle 'oneOf' keyword
    if "oneOf" in schema:
        for subschema in schema["oneOf"]:
            collect_properties(subschema, resolver, mappers, result)
        return result

    # Warn that we don't support NOT
    if "not" in schema:
        raise NotImplementedError("JSON schema negations are not supported")

    # Handle 'properties' keyword
    if "properties" in schema:
        for name, definition in schema["properties"].items():
            info = PropertyInfo.from_json_schema(name, definition)
            info.mappers = mappers.get(name)
            result[name] = info

    return result


class ModelMetaHelpers(object):
    """Helper methods for the ModelMeta_ metaclass. These are defined here
    and not in ModelMeta_ to ensure that they do not appear as methods of
    the classes that ModelMeta_ constructs.
    """

    @staticmethod
    def add_clone_method(dct):
        """Extends the class being constructed with a ``clone()`` method
        that returns a shallow copy of the object.

        If the dictionary already has a ``clone()`` method, no new method
        will be added and the original method will be left intact.

        Parameters:
            dct (dict): the class dictionary
        """
        if "clone" in dct:
            return

        def clone(self):
            """Returns a shallow copy of the object."""
            return self.__class__(json=self.json)

        dct["clone"] = clone

    @staticmethod
    def add_json_property(dct):
        """Extends the class being constructed with a ``json`` property
        that contains the instance data in JSON format. Setting the property
        will trigger a full JSON schema validation.

        Parameters:
            dct (dict): the class dictionary
        """
        orig_init = dct.get("__init__")

        def __init__(self, json=None, *args, **kwds):
            self.__dict__["_json"] = {}
            self.__dict__["_validation_suppressed"] = False
            if orig_init is not None:
                orig_init(self, *args, **kwds)
            if json is not None:
                self.json = json

        if orig_init and hasattr(orig_init, "__doc__"):
            __init__.__doc__ = orig_init.__doc__

        @property
        def json(self):
            """The value of the object in JSON format"""
            return self._json

        @json.setter
        def json(self, value):
            if self._json is value:
                return
            self._json = value
            if not self._validation_suppressed:
                self.validate()

        @classmethod
        def from_json(cls, data, validate=True):
            """Constructs this model object from its JSON representation.

            Parameters:
                data (object): the JSON representation of the model object
                validate (bool): whether to validate the JSON
                    representation before trying to set it on the model
                    object
            """
            if validate:
                return cls(json=data)
            else:
                result = cls()
                with result.suppressed_validation():
                    result.json = data
                return result

        dct.update(__init__=__init__, from_json=from_json, json=json)

    @staticmethod
    def add_proxy_property(dct: Dict, name: str, property_info: PropertyInfo):
        """Extends the class being constructed with a single proxy property
        that accesses an entry in the underlying JSON object directly.

        Parameters:
            dct: the class dictionary
            name: the name of the property
            property_info: an object that describes the underlying JSON property
                based on the schema
        """

        if property_info.mappers is None:

            def getter(self):
                try:
                    return self._json[name]
                except KeyError:
                    raise AttributeError(name) from None

            def setter(self, value):
                self._json[name] = value

        else:
            from_json, to_json = property_info.mappers

            def getter(self):
                try:
                    raw_value = self._json[name]
                except KeyError:
                    raise AttributeError(name) from None
                return from_json(raw_value)

            def setter(self, value):
                self._json[name] = to_json(value)

        def deleter(self):
            del self._json[name]

        getter.__name__ = name
        setter.__name__ = name
        deleter.__name__ = name
        doc = property_info.description or None

        dct[name] = property(getter, setter, deleter, doc)

    @classmethod
    def add_proxy_properties(cls, dct, property_info):
        """Extends the class being constructed with proxy properties that
        access specific entries in the JSON object directly.

        Parameters:
            dct (dict): the class dictionary
            property_info (dict): dictionary mapping property names to
                PropertyInfo_ objects that describe the underlying JSON
                property based on the schema
        """
        for name, info in property_info.items():
            cls.add_proxy_property(dct, name, info)

    @staticmethod
    def add_special_methods(dct):
        """Adds some special methods to the class dictionary that allows
        attributes of the wrapped JSON object to be accessed with member
        and dictionary notation.
        """

        def __contains__(self, key):
            return key in self._json

        def __getitem__(self, key):
            return self._json[key]

        for name in ["__contains__", "__getitem__"]:
            if name not in dct:
                dct[name] = locals()[name]

    @staticmethod
    def add_suppressed_validation_context_manager(dct):
        """Adds a ``suppressed_validation()`` context manager to the given
        class dictionary.

        If the dictionary already has a ``suppressed_validation()`` context
        manager, no modification will be performed.

        Parameters:
            dct (dict): the class dictionary
        """
        if "suppressed_validation" in dct:
            return

        @contextmanager
        def suppressed_validation(self):
            """Context manager that suppresses validation on the model
            object while the execution is within the context.
            """
            old_value = self._validation_suppressed
            self._validation_suppressed = True
            try:
                yield
            finally:
                self._validation_suppressed = old_value

        dct["suppressed_validation"] = suppressed_validation

    @staticmethod
    def add_update_from_method(dct):
        """Extends the class being constructed with an ``update_from()`` method
        that updates all properties of this object from the properties of
        another, similarly typed object.

        If the dictionary already has an ``update_from()`` method, no new
        method will be added and the original method will be left intact.

        Parameters:
            dct (dict): the class dictionary
        """
        if "update_from" in dct:
            return

        def update_from(self, other):
            """Updates the properties of the object from another object."""
            self.json = other.json

        dct["update_from"] = update_from

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

        json_schema_validator_class = jsonschema.validators.validator_for(schema)
        json_schema_validator = json_schema_validator_class(schema, resolver=resolver)

        def validate(self, *args, **kwds):
            """Validates this class instance against its JSON schema.

            Throws:
                jsonschema.ValidationError: if the class instance does not
                    match its schema
            """
            json_schema_validator.validate(self._json)
            if orig_validator is not None:
                return orig_validator(*args, **kwds)

        if orig_validator and hasattr(orig_validator, "__doc__"):
            validate.__doc__ = orig_validator.__doc__

        dct["validate"] = validate

    @staticmethod
    def bases_have_schema(bases):
        """Returns whether any of the given base classes uses ModelMeta_ as
        its metaclass.

        Parameters:
            bases (List[type]): list of the base classes

        Returns:
            bool: whether at least one of the base classes uses ModelMeta_
                as its metaclass
        """
        return any(getattr(base, "__metaclass_is_ModelMeta__", False) for base in bases)

    @classmethod
    def find_property_mappers(cls, dct, bases):
        """Finds the specification of the property mappers that the class being
        constructed must make use of. This is done by looking up the ``mappers``
        attribute in the ``__meta__`` class embedded in the class definition.

        Returns:
            dict: a dictionary mapping names of properties to be generated in
                the class to a pair where the first item is a function that
                maps the property _from_ its JSON representation to its real
                value (used during deserialization) and the second item is a
                function that maps the property _to_ its JSON representation
                from its real value (used during serialization).
        """
        dct = dct.get("__meta__")
        if hasattr(dct, "mappers"):
            return dct.mappers
        else:
            return {}

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
        bases_have_schema = ModelMetaHelpers.bases_have_schema(bases)

        dct = dct.get("__meta__")
        schema = getattr(dct, "schema", None)
        resolver = getattr(dct, "ref_resolver", None)

        if schema is not None:
            if resolver is None:
                resolver = jsonschema.RefResolver.from_schema(
                    schema, handlers={"http": flockwave_schema_ref_resolver}
                )
            elif not isinstance(resolver, jsonschema.RefResolver):
                if callable(resolver):
                    resolver = jsonschema.RefResolver.from_schema(
                        schema, handlers={"http": resolver}
                    )
                else:
                    resolver = jsonschema.RefResolver.from_schema(
                        schema, handlers=resolver
                    )

        if schema is not None or bases_have_schema:
            return schema, resolver
        else:
            raise TypeError(
                "Model classes must either have a 'schema' "
                "attribute or derive from another model class "
                "with a schema"
            )

    @staticmethod
    def mark_metaclass(dct):
        """Marks the given class dictionary to remember that the class was
        constructed by the ModelMeta_ metaclass.

        Parameters:
            dct (dict): the class dictionary
        """
        dct["__metaclass_is_ModelMeta__"] = True


class ModelMeta(type):
    """Metaclass for our model objects. Adds JSON validation automatically
    when the model objects are constructed from JSON.
    """

    def __new__(cls, clsname, bases, dct):
        """Metaclass constructor method.

        Arguments:
            clsname (str): the name of the class being constructed
            bases (list of type): base classes for the class being
                constructed
            dct (dict): namespace of the class body
        """
        bases_have_schema = ModelMetaHelpers.bases_have_schema(bases)
        schema, resolver = ModelMetaHelpers.find_schema_and_resolver(dct, bases)
        mappers = ModelMetaHelpers.find_property_mappers(dct, bases)
        if schema is not None:
            if not bases_have_schema:
                ModelMetaHelpers.add_json_property(dct)
                ModelMetaHelpers.add_special_methods(dct)
                property_info = collect_properties(schema, resolver, mappers)
                ModelMetaHelpers.add_proxy_properties(dct, property_info)
                ModelMetaHelpers.add_clone_method(dct)
                ModelMetaHelpers.add_update_from_method(dct)
                ModelMetaHelpers.add_suppressed_validation_context_manager(dct)
                ModelMetaHelpers.mark_metaclass(dct)
            ModelMetaHelpers.add_validator_method(dct, schema, resolver)
        return type.__new__(cls, clsname, bases, dct)
