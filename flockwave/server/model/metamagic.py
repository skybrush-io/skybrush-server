"""Metaclass (and related things) for the Flockwave object model.

The metaclass defined here allows us to create classes that generate
properties for themselves and validate themselves automatically based on
a JSON schema description.
"""

import jsonschema

__all__ = ("ModelMeta", )


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

        @property
        def json(self):
            """The value of the object in JSON format"""
            return self._json

        @json.setter
        def json(self, value):
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
            json=json
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
