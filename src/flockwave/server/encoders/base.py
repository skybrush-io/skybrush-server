"""Base class and interface specification for message encoders in the
Flockwave server.
"""

from abc import ABCMeta, abstractmethod
from typing import Generic, TypeVar

__all__ = ("Encoder",)

T = TypeVar("T")


class Encoder(Generic[T], metaclass=ABCMeta):
    """Interface specification for message encoders that can encode and
    decode Flockwave messages or other objects in a specific message
    format.
    """

    @abstractmethod
    def dumps(self, obj: T) -> bytes:
        """Converts the given object into its encoded representation.

        Parameters:
            obj (object): the object to encode

        Returns:
            a byte-based representation of the given object
        """
        raise NotImplementedError

    @abstractmethod
    def loads(self, data: bytes) -> T:
        """Loads an encoded object from the given raw representation.

        Parameters:
            data: the raw bytes to decode

        Returns:
            the constructed object
        """
        raise NotImplementedError
