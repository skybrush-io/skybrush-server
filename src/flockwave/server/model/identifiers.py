from baseconv import base64
from random import getrandbits

__all__ = ("default_id_generator",)


def default_id_generator() -> str:
    """Default ID generator that generates 60-bit random integers and
    encodes them using base64, yielding ten-character random identifiers.
    """
    return base64.encode(getrandbits(60))
