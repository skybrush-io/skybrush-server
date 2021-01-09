from flockwave.spec.schema import get_complex_object_schema

from .metamagic import ModelMeta

__all__ = ("TransportOptions",)


class TransportOptions(metaclass=ModelMeta):
    """Class representing the transport options attached to some of the UAV
    command requests.
    """

    class __meta__:
        schema = get_complex_object_schema("transportOptions")
