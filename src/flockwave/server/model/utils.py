from base64 import b64decode, b64encode
from typing import Callable, Union

from .metamagic import MapperPair

__all__ = ("as_base64", "coerce", "scaled_by")


def as_base64() -> MapperPair:
    """Returns a property mapper function pair that can be used to represent
    a byte array as a base64-encoded string when saving it into JSON.
    """

    def from_json(value):
        return None if value is None else b64decode(value.encode("ascii"))

    def to_json(value):
        return None if value is None else b64encode(value).decode("ascii")

    return from_json, to_json


as_base64 = as_base64()


def coerce(type: Callable) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    has to be coerced into a specific type before saving it into JSON.
    """
    return type, type


def coerce_optional(type: Callable) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    has to be coerced into a specific type before saving it into JSON, assuming
    that undefined (null) values are allowed in JSON.
    """

    def from_json(value):
        return None if value is None else type(value)

    def to_json(value):
        return None if value is None else type(value)

    return from_json, to_json


def scaled_by(factor: Union[int, float]) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    of a numeric property is scaled up by a factor and then cast to an integer
    when it is stored in JSON.
    """
    factor = float(factor)

    def from_json(value: Union[int, float]) -> float:
        return value / factor

    def to_json(value: float) -> int:
        return int(round(value * factor))

    return from_json, to_json
