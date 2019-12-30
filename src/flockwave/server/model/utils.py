from typing import Callable, Union

from .metamagic import MapperPair

__all__ = ("coerce", "scaled_by")


def coerce(type: Callable) -> MapperPair:
    """Returns a property mapper function pair that can be used when the value
    has to be coerced into a specific type before saving it into JSON.
    """
    return type, type


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
