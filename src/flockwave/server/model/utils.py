from base64 import b64decode, b64encode
from enum import Enum, IntEnum
from typing import Callable, Optional, Type

from .metamagic import MapperPair

__all__ = ("as_base64", "coerce", "enum_to_json", "optionally_scaled_by", "scaled_by")


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


def enum_to_json(type: Type[Enum]) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    is an enum and has to be replaced with its string or integer representation
    before saving it into JSON.
    """

    def from_json(value):
        return type(value)

    if issubclass(type, IntEnum):

        def to_json(value):
            return int(value.value)

    else:

        def to_json(value):
            return str(value.value)

    return from_json, to_json


def scaled_by(factor: float) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    of a numeric property is scaled up by a factor and then cast to an integer
    when it is stored in JSON.
    """
    factor = float(factor)

    def from_json(value: float) -> float:
        return value / factor

    def to_json(value: float) -> int:
        return int(round(value * factor))

    return from_json, to_json


def optionally_scaled_by(factor: float) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    of a numeric property is scaled up by a factor and then cast to an integer
    when it is stored in JSON. Also handles None transparently.
    """
    factor = float(factor)

    def from_json(value: Optional[float]) -> Optional[float]:
        return value / factor if value is not None else None

    def to_json(value: Optional[float]) -> Optional[int]:
        return int(round(value * factor)) if value is not None else None

    return from_json, to_json
