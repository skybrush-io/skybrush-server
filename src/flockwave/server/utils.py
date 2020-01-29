"""Utility functions that do not fit elsewhere."""

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from functools import partial as partial_
from inspect import Parameter, signature
from typing import Any, Callable


__all__ = (
    "datetime_to_unix_timestamp",
    "identity",
    "is_timezone_aware",
    "itersubclasses",
    "keydefaultdict",
    "nop",
    "overridden",
)


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
        return partial_(func, *args)
    else:
        return partial_(func, *args, **kwds)


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


class keydefaultdict(defaultdict):
    """defaultdict subclass that passes the key of the item being created
    to the default factory.
    """

    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        else:
            ret = self[key] = self.default_factory(key)
            return ret


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


@contextmanager
def overridden(dictionary, **kwds):
    """Context manager that updates a dictionary with some key-value
    pairs, restoring the original values in the dictionary when the
    context is exited.
    """
    names = list(kwds.keys()) if kwds else []
    originals = {}

    try:
        for name in names:
            if name in dictionary:
                originals[name] = dictionary[name]
            dictionary[name] = kwds[name]
        yield
    finally:
        for name in names:
            if name in originals:
                dictionary[name] = originals[name]
            else:
                del dictionary[name]
