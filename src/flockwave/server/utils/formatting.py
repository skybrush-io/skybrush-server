"""Formatting-related utility functions."""

from datetime import datetime, timedelta
from typing import Callable, Sequence, TypeVar, Union

from flockwave.gps.vectors import GPSCoordinate

__all__ = (
    "format_gps_coordinate",
    "format_latitude_for_nmea_gga_message",
    "format_longitude_for_nmea_gga_message",
    "format_list_nicely",
    "format_number_nicely",
    "format_uav_ids_nicely",
    "format_timedelta_nicely",
    "format_timestamp_nicely",
)

T = TypeVar("T")


def format_gps_coordinate(coord: GPSCoordinate) -> str:
    """Formats a GPS coordinate in a huamn-readable way."""
    if coord.amsl is not None:
        return f"{coord.lat:.7f}°, {coord.lon:.7f}°, {coord.amsl:.1f}m AMSL"
    elif coord.agl is not None:
        return f"{coord.lat:.7f}°, {coord.lon:.7f}°, {coord.amsl:.1f}m AGL"
    else:
        return f"{coord.lat:.7f}°, {coord.lon:.7f}°"


def format_latitude_for_nmea_gga_message(lat: float) -> tuple[str, str]:
    """Formats a latitude in a way that is suitable for NMEA GGA messages.

    Args:
        lat: the latitude

    Returns:
        the formatted coordinate and the sign (North or South)
    """
    sign = "S" if lat < 0 else "N"
    deg, min_frac = divmod(abs(lat), 1)
    return f"{int(deg):02}{min_frac * 60:07.4f}", sign


def format_longitude_for_nmea_gga_message(lon: float) -> tuple[str, str]:
    """Formats a longitude in a way that is suitable for NMEA GGA messages.

    Args:
        lon: the longitude

    Returns:
        the formatted coordinate and the sign (East or West)
    """
    sign = "W" if lon < 0 else "E"
    deg, min_frac = divmod(abs(lon), 1)
    return f"{int(deg):03}{min_frac * 60:07.4f}", sign


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


def format_timedelta_nicely(delta: Union[float, timedelta]) -> str:
    """Formats a Python timedelta object or a float containing seconds; the
    result will be separated into hours, minutes and seconds.
    """
    dt = delta.total_seconds() if isinstance(delta, timedelta) else delta
    sign = "-" if dt < 0 else ""
    minutes, seconds = divmod(abs(dt), 60)
    minutes = int(minutes)
    hours, minutes = divmod(minutes, 60)
    seconds = round(seconds, 3)
    maybe_zero = "0" if seconds < 10 else ""
    if seconds.is_integer():
        seconds = int(seconds)
        return f"{sign}{hours:02}:{minutes:02}:{maybe_zero}{seconds}"
    else:
        return f"{sign}{hours:02}:{minutes:02}:{maybe_zero}{seconds:.3}"


def format_timestamp_nicely(timestamp: Union[float, datetime]) -> str:
    """Formats a UNIX timestamp or a Python datetime object nicely, including
    the date part of the timestamp as well.
    """
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
