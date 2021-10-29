from typing import Any, Callable, ClassVar

from flockwave.spec.schema import get_complex_object_schema

from .metamagic import ModelMeta

__all__ = ("TransportOptions",)


class TransportOptions(metaclass=ModelMeta):
    """Class representing the transport options attached to some of the UAV
    command requests.
    """

    class __meta__:
        schema = get_complex_object_schema("transportOptions")

    from_json: ClassVar[Callable[[Any], "TransportOptions"]]

    @classmethod
    def is_broadcast(cls, transport: Any) -> bool:
        """Returns whether the given object is a transport options object (with
        type checking) and whether it indicates that we should broadcast a
        particular message.

        This function is safe to be called with any type of object.
        """
        if isinstance(transport, cls):
            return bool(getattr(transport, "broadcast", False))
        else:
            return False

    @classmethod
    def is_secondary(cls, transport: Any) -> bool:
        """Returns whether the given object is a transport options object (with
        type checking) and whether it indicates that the message should be sent
        over some non-primary channel. Non-primary channels are channels with
        indices larger than zero.

        This function is safe to be called with any type of object.
        """
        if isinstance(transport, cls):
            index = getattr(transport, "channel", 0)
            return isinstance(index, (int, float)) and index > 0
        else:
            return False
