"""Classes for JSON-based message encoding and decoding."""

from __future__ import absolute_import

from datetime import datetime
from enum import Enum

import json

from .base import Encoder

__all__ = ("JSONEncoder", )


class JSONEncoder(Encoder):
    """Custom JSON encoder and decoder function to be used by JSON-based
    communication channels.

    The JSON format used by this encoder ensures that there are no newlines
    in the encoded JSON objects. Therefore, newlines can safely be used as
    message delimiters.
    """

    def __init__(self):
        """Constructor."""
        self.encoder = json.JSONEncoder(
            separators=(",", ":"), sort_keys=False, indent=None,
            default=self._encode
        )
        self.decoder = json.JSONDecoder()

    def _encode(self, obj):
        """Encodes an object that could otherwise not be encoded into JSON.

        This function performs the following conversions:

        - ``datetime.datetime`` objects are converted into a standard
          ISO-8601 string representation

        - Enum instances are converted to their names

        - Objects having a ``json`` property will be replaced by the value
          of this property

        Parameters:
            obj (object): the object to encode

        Returns:
            object: the JSON representation of the object
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Enum):
            return obj.name
        elif hasattr(obj, "json"):
            return obj.json
        else:
            raise TypeError("cannot encode {0!r} into JSON".format(obj))

    def dumps(self, obj, *args, **kwds):
        """Converts the given object into a JSON string representation.
        Additional positional or keyword arguments that may be passed by
        Socket.IO are silently ignored.

        Parameters:
            obj (object): the object to encode into a JSON string

        Returns:
            str: a string representation of the given object in JSON
        """
        return self.encoder.encode(obj)

    def loads(self, data, *args, **kwds):
        """Loads a JSON-encoded object from the given string representation.
        Additional positional or keyword arguments that may be passed by
        Socket.IO are silently ignored.

        Parameters:
            data (str): the string to decode

        Returns:
            object: the constructed object
        """
        return self.decoder.decode(data)
