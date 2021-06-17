from inspect import isawaitable
from trio import open_nursery
from typing import Any, Awaitable, Dict, TypeVar

__all__ = ("wait_for_dict_items",)

T = TypeVar("T")

DictT = TypeVar("DictT", bound=Dict)


async def wait_for_dict_items(obj: DictT) -> DictT:
    """Iterates over all key-value pairs of a dictionary and awaits all values
    that are awaitables, re-assigning their results to the appropriate keys
    in the dict.

    Exceptions raised by the awaitables are caught and assigned.
    """
    async with open_nursery() as nursery:
        for key, value in obj.items():
            if isawaitable(value):
                nursery.start_soon(_wait_safely_and_put, value, obj, key)
    return obj


async def _wait_safely_and_put(obj: Awaitable[Any], d: Dict[T, Any], key: T):
    try:
        d[key] = await obj
    except Exception as ex:
        d[key] = ex
