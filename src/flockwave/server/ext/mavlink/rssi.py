from enum import Enum
from typing import Optional

__all__ = ("RSSIMode", "rtcm_counter_to_rssi")


class RSSIMode(Enum):
    """Specifies how a given MAVLink network will derive the RSSI (received
    signal strength indicator) value of its own drones.
    """

    # If you extend this enum, do not forget to update the extension schema

    NONE = "none"
    """No RSSI value will be derived."""

    RADIO_STATUS = "radio_status"
    """The RSSI value will be derived from the MAVLink RADIO_STATUS message."""

    RTCM_COUNTERS = "rtcm_counters"
    """The RSSI value will be derived from the RTCM message counters embedded
    in Skybrush-specific status packets. Works with a Skybrush firmware only.
    """


def rtcm_counter_to_rssi(value: Optional[int]) -> Optional[int]:
    """Converts an RTCM message counter to a simulated RSSI value.

    The conversion is done as follows:

    - ``None`` is left as is.
    - No RTCM messages are converted to 0%.
    - 10 RTCM messages or more are converted to 100%.
    - Values in between are linearly interpolated.
    """
    if value is None:
        return None
    elif value <= 0:
        return 0
    elif value >= 10:
        return 100
    else:
        return value * 10
