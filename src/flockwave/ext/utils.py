from collections import defaultdict
from functools import partial as partial_, wraps
from inspect import Parameter, signature

__all__ = ("bind", "cancellable", "keydefaultdict")


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


def cancellable(func):
    """Decorator that extends an async function with an extra `cancel_scope`
    keyword argument and makes the function enter the cancel scope.
    """

    @wraps(func)
    async def decorated(*args, cancel_scope, **kwds):
        with cancel_scope:
            return await func(*args, **kwds)

    decorated._cancellable = True

    return decorated


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
