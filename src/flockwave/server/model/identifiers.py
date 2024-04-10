from baseconv import base64
from bidict import bidict
from random import getrandbits

from typing import Callable, Generic, Optional, Union, TypeVar

from flockwave.spec.ids import make_valid_object_id

__all__ = (
    "create_object_id_generator_for_ints",
    "default_id_generator",
    "UniqueIdGenerator",
)


T = TypeVar("T")


def default_id_generator() -> str:
    """Default ID generator that generates 60-bit random integers and
    encodes them using base64, yielding ten-character random identifiers.
    """
    return base64.encode(getrandbits(60))


class UniqueIdGenerator(Generic[T]):
    _formatter: Callable[[T], str]
    """Callable that takes a value from the input domain of the ID generator and
    returns the corresponding string ID.
    """

    _validator: Optional[Callable[[str], str]] = None
    """Validator function that takes a generated string ID and possibly returns
    another string that is guaranteed to satisfy some validation criteria.
    Called every time a new ID is generated using the formatter. `None` means
    an identity function.
    """

    _value_to_id: bidict[T, str]
    """Bidirectional cache mapping values from the input domain to the
    corresponding string IDs.
    """

    def __init__(
        self,
        formatter: Union[str, Callable[[T], str]] = "{0}",
        validator: Optional[Callable[[str], str]] = None,
    ):
        """Constructor.

        Args:
            formatter: the ID formatter function that takes a value from the
                input domain and returns the corresponding desired string ID
            validator: optional validator function to call on the generated
                string ID
        """
        self._value_to_id = bidict()
        self.set_formatter(formatter)
        self._validator = validator

    def lookup(self, value: T) -> str:
        """Returns the unique ID corresponding to the given value.

        This function is cached; the ID is generated only once when the
        corresponding input value is seen for the first time.
        """
        result = self._value_to_id.get(value)
        if result is None:
            result = self._formatter(value)
            if self._validator:
                result = self._validator(result)
            self._value_to_id[value] = result
        return result

    def reverse_lookup(self, id: str) -> Optional[T]:
        """Returns the original value that was mapped to the given ID, or
        `None` if the given ID was never returned from this generator.
        """
        return self._value_to_id.inverse.get(id)

    def set_formatter(self, formatter: Union[str, Callable[[T], str]]):
        """Sets the formatter used by the unique ID generator.

        Changing the formatter while some IDs were already generated is
        possible, but old (already cached) IDs will not be affected.

        Args:
            formatter: the new formatter function that takes a value from the
                input domain of the ID generator and returns the corresponding
                ID, or a Python format string whose `format()` method will be
                called to generate the ID.
        """
        if isinstance(formatter, str):
            self._formatter = formatter.format
        else:
            self._formatter = formatter


def create_object_id_generator_for_ints(
    formatter: Union[str, Callable[[int], str]] = "{0}",
) -> UniqueIdGenerator[int]:
    """Creates an ID generator object that generates IDs for UAVs given a
    formatter or format string.
    """
    return UniqueIdGenerator(formatter, make_valid_object_id)
