"""Formatting-related utility functions."""

from datetime import datetime
from typing import Callable, Sequence, TypeVar, Union

__all__ = ("format_list_nicely", "format_number_nicely", "format_uav_ids_nicely")

T = TypeVar("T")


def format_list_nicely(
    items: Sequence[T], *, max_items: int = 5, item_formatter: Callable[[T], str] = str
) -> str:
    if not items:
        return ""

    num_items = len(items)
    excess_items = max(0, num_items - max_items)
    if excess_items:
        return (
            ", ".join(item_formatter(item) for item in items[:max_items])
            + f" and {excess_items} more"
        )

    if num_items == 1:
        return item_formatter(items[0])
    else:
        return (
            ", ".join(item_formatter(item) for item in items[:-1])
            + " and "
            + item_formatter(items[-1])
        )


def format_number_nicely(value: float) -> str:
    """Formats a float nicely, stripping trailing zeros and avoiding scientific
    notation where possible.
    """
    return f"{value:.7f}".rstrip("0").rstrip(".")


def format_timestamp_nicely(timestamp: Union[float, datetime]) -> str:
    """Formats a UNIX timestamp or a Python datetime object nicely."""
    dt = (
        timestamp
        if isinstance(timestamp, datetime)
        else datetime.fromtimestamp(timestamp)
    )
    return dt.isoformat().replace("T", " ")


def format_uav_ids_nicely(ids: Sequence[str], *, max_items: int = 5) -> str:
    if not ids:
        return "no UAVs"
    elif len(ids) == 1:
        return "UAV " + format_list_nicely(ids, max_items=max_items)
    else:
        return "UAVs " + format_list_nicely(ids, max_items=max_items)
