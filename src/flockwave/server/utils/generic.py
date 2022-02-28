"""Generic utility functions that do not fit elsewhere."""

from colour import Color
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from inspect import Parameter, signature
from itertools import tee
from operator import mul
from typing import (
    Any,
    Callable,
    Iterable,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)


__all__ = (
    "bind",
    "clamp",
    "color_to_rgb565",
    "color_to_rgb8_triplet",
    "consecutive_pairs",
    "constant",
    "datetime_to_unix_timestamp",
    "divide_by",
    "identity",
    "is_timezone_aware",
    "itersubclasses",
    "longest_common_prefix",
    "maybe_round",
    "multiply_by",
    "nop",
    "once",
    "optional_float",
    "optional_int",
    "overridden",
    "to_uppercase_string",
)


T = TypeVar("T")


def bind(func, args=None, kwds=None, *, partial=False):
    """Variant of `functools.partial()` that allows the argument list to
    be longer than the number of arguments accepted by the function if
    `partial` is set to `True`. If this is the case, the argument list
    will be truncated to the number of positional arguments accepted by
    the function.

    Parameters:
        args: the positional arguments to bind to the function
        kwds: the keyword arguments to bind to the function
    """
    if not args and not kwds:
        return func

    if partial:
        num_args = 0
        for parameter in signature(func).parameters.values():
            if parameter.kind == Parameter.VAR_POSITIONAL:
                num_args = len(args)
                break
            elif parameter.kind in (Parameter.KEYWORD_ONLY, Parameter.VAR_KEYWORD):
                pass
            else:
                num_args += 1

        args = args[:num_args]

    if kwds is None:
        return partial(func, *args)
    else:
        return partial(func, *args, **kwds)


def clamp(value: T, lo: T, hi: T) -> T:
    """Clamps the given value between a minimum and a maximum allowed value
    (both inclusive).
    """
    return max(min(value, hi), lo)  # type: ignore


def color_to_rgb565(color: Color) -> int:
    """Converts a color object into its RGB565 representation.

    Parameters:
        color: the color to convert

    Returns:
        the color in its RGB565 representation
    """
    red, green, blue = color_to_rgb8_triplet(color)
    return (
        (((red >> 3) & 0x1F) << 11)
        + (((green >> 2) & 0x3F) << 5)
        + ((blue >> 3) & 0x1F)
    )


def color_to_rgb8_triplet(color: Color) -> Tuple[int, int, int]:
    """Converts a color object into its RGB8 triplet representation.

    Parameters:
        color: the color to convert

    Returns:
        the color in its RGB8 triplet representation
    """
    return tuple(round(x * 255) for x in color.rgb)  # type: ignore


T = TypeVar("T")


def consecutive_pairs(iterable: Iterable[T]) -> Iterable[Tuple[T, T]]:
    """Given an iterable, returns a generator that generates consecutive
    pairs of items from the iterable.

    Parameters:
        iterable: the iterable

    Yields:
        pairs of consecutive items from the iterable
    """
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


def constant(x: Any) -> Callable[..., Any]:
    """Function factory that returns a function that accepts an arbitrary
    number of arguments and always returns the same constant.

    Parameters:
        x (object): the constant to return

    Returns:
        callable: a function that always returns the given constant,
            irrespectively of its input
    """

    def func(*args, **kwds) -> Any:
        return x

    return func


def datetime_to_unix_timestamp(dt: datetime) -> float:
    """Converts a Python datetime object to a Unix timestamp, expressed in
    the number of seconds since the Unix epoch.

    The datetime object must be timezone-aware to avoid confusion with the
    time zones.

    Parameters:
        dt: the Python datetime object

    Returns:
        the time elapsed since the Unix epoch, in seconds

    Raises:
        ValueError: if the given datetime is not timezone-aware
    """
    if not is_timezone_aware(dt):
        raise ValueError("datetime object must be timezone-aware")
    return dt.timestamp()


def divide_by(value: float) -> Callable[[float], float]:
    """Returns a function that divides every number received as an input
    with the given value.
    """
    return partial(mul, 1.0 / value)


def identity(obj: Any) -> Any:
    """Identity function that returns its input argument."""
    return obj


def is_timezone_aware(dt: datetime) -> bool:
    """Checks whether the given Python datetime object is timezone-aware
    or not.

    Parameters:
        dt: the Python datetime object

    Returns:
        ``True`` if the given object is timezone-aware, ``False`` otherwise
    """
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def itersubclasses(cls):
    """Iterates over all the subclasses of the given class in a depth-first
    manner.

    Parameters:
        cls (type): the (new-style) Python class whose subclasses we are
            iterating over

    Yields:
        type: the subclasses of the given class in DFS order, including
            the class itself.
    """
    queue = [cls]
    while queue:
        cls = queue.pop()
        yield cls
        queue.extend(cls.__subclasses__())


def longest_common_prefix(strings: Sequence[str]) -> str:
    """Finds the longest common prefix of a sequence of strings."""
    if not strings:
        return ""

    shortest_string = min(strings, key=len)
    for i, char in enumerate(shortest_string):
        for other in strings:
            if other[i] != char:
                return shortest_string[:i]

    return shortest_string


def maybe_round(value: Optional[float], ndigits: int = 0) -> Optional[float]:
    """Rounds the given value to the given number of digits if it is not
    ``None``; returns ``None`` otherwise.
    """
    return round(value, ndigits) if value is not None else None


def multiply_by(term: float) -> Callable[[float], float]:
    """Returns a function that multiplies every number received as an input
    with the given term.
    """
    return partial(mul, term)


def nop(*args, **kwds):
    """Dummy function that can be called with any number of arguments and
    does not return anything.
    """
    pass


def once(func):
    """Decorator that decorates a function and allows it to be called only
    once. Subsequent attempts to call the function will throw an exception.
    """

    def wrapped(*args, **kwds):
        if wrapped.called:
            raise RuntimeError("{!r} can be called only once".format(func))

        wrapped.called = True
        return func(*args, **kwds)

    wrapped.called = False
    return wrapped


def optional_float(x: Any) -> Optional[float]:
    """Converts the given value into a float, unless it is `None`, in which
    case it is returned intact.

    Raises:
        ValueError: if the given value cannot be converted into a float
    """
    return float(x) if x is not None else None


def optional_int(x: Any) -> Optional[int]:
    """Converts the given value into an integer, unless it is `None`, in which
    case it is returned intact.

    Raises:
        ValueError: if the given value cannot be converted into an integer
    """
    return int(x) if x is not None else None


@contextmanager
def overridden(obj: Any, **kwds):
    """Context manager that updates an object or dictionary with some key-value
    pairs, restoring the original values in the object or dictionary when the
    context is exited.

    When the input object is a dictionary, the given keyword arguments will be
    registered as keys and values (obviously). When the input object is _not_
    a dictionary, the given keyword arguments will be set on the object as
    _attributes_. In both cases, the original values are restored when the
    context exits.
    """
    names = list(kwds.keys()) if kwds else []
    originals = {}

    is_dict = isinstance(obj, dict)

    try:
        if is_dict:
            for name in names:
                if name in obj:
                    originals[name] = obj[name]
                obj[name] = kwds[name]
        else:
            for name in names:
                if hasattr(obj, name):
                    originals[name] = getattr(obj, name)
                setattr(obj, name, kwds[name])

        yield

    finally:
        if is_dict:
            for name in names:
                if name in originals:
                    obj[name] = originals[name]
                else:
                    del obj[name]
        else:
            for name in names:
                if name in originals:
                    setattr(obj, name, originals[name])
                else:
                    delattr(obj, name)


def to_uppercase_string(value: Any) -> str:
    """Converts the given value into a string and casts the string into
    uppercase.
    """
    return str(value).upper()
