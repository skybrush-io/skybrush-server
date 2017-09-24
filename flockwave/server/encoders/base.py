"""Base class and interface specification for message encoders in the
Flockwave server.
"""

from abc import ABCMeta, abstractmethod
from future.utils import with_metaclass

__all__ = ("Encoder", )


class Encoder(with_metaclass(ABCMeta, object)):
    """Interface specification for message encoders that can encode and
    decode Flockwave messages in a specific message format.
    """

    @abstractmethod
    def dumps(self, obj):
        """Converts the given object into its encoded representation.

        Parameters:
            obj (object): the object to encode

        Returns:
            str: a string representation of the given object
        """
        raise NotImplementedError

    @abstractmethod
    def loads(self, obj):
        """Loads an encoded object from the given string representation.

        Parameters:
            data (str): the string to decode

        Returns:
            object: the constructed object
        """
        raise NotImplementedError
