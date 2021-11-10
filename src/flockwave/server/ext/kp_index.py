"""Extension that registers a weather provider that provides planetary K-index
values from a chosen data source.
"""

import httpx

from bisect import bisect
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from time import monotonic
from trio import fail_after, Lock, sleep_forever, TooSlowError
from typing import (
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.weather import Weather


class KpIndexData:
    """Object representing the last Kp-index data downloaded from a data source.
    A single download typically yields Kp-index values for several dates; this
    class stores the entire download in parsed format and returns the Kp-index
    value for a given date if the downloaded data contains the date.
    """

    INVALID: ClassVar["KpIndexData"]

    min_timestamp: int
    """Smallest UNIX timestamp for which we have a valid Kp-index estimate."""

    max_timestamp: int
    """Largest UNIX timestamp for which we have a valid Kp-index estimate."""

    _timestamps: List[int]
    """Sorted list of timestamps; each timestamp refers to
    the midpoint of an interval for which we have a Kp-index estimate.
    """

    _values: List[float]
    """List of Kp-index values, one value for each timestamp in the timestamp
    array.
    """

    @classmethod
    def from_midpoints_and_values(
        cls, midpoints_and_values: Sequence[Tuple[int, float]]
    ):
        """Creates a new dataset from a list of timestamps at interval midpoints,
        and the corresponding Kp-index estimates.
        """
        result = cls()
        timestamps, values = tuple(zip(*sorted(midpoints_and_values)))
        result._timestamps = timestamps  # type: ignore
        result._values = values  # type: ignore
        result._update_min_max_timestamps()
        return result

    def __init__(self):
        """Constructor.

        Creates an empty object that does not contain a Kp-index for any of the
        dates.
        """
        self.min_timestamp = self.max_timestamp = 0
        self._timestamps = []
        self._values = []

    def get_kp_index_for(self, timestamp: int) -> Optional[float]:
        """Returns an (estimated or definite) Kp-index for the given timestamp,
        measured in seconds from the UNIX epoch, or `None` if there is no data
        for the given timestamp.
        """
        if not self._timestamps:
            return None

        if timestamp < self.min_timestamp or timestamp > self.max_timestamp:
            return None

        index = bisect(self._timestamps, timestamp)

        if index >= len(self._timestamps):
            return self._values[-1]

        if index <= 0:
            return self._values[0]

        diff_before = timestamp - self._timestamps[index - 1]
        diff_after = self._timestamps[index] - timestamp
        if diff_before <= diff_after:
            return self._values[index - 1]
        else:
            return self._values[index]

    def _update_min_max_timestamps(self) -> None:
        """Updates the minimum and maximum timestamps of the object based on the
        stored midpoints, assuming that the intervals are sorted and
        non-overlapping but they touch each other at the boundaries.
        """
        ts = self._timestamps
        if not ts:
            self.min_timestamp = self.max_timestamp = 0
        else:
            self.min_timestamp = int(floor(ts[0] - (ts[1] - ts[0]) / 2))
            self.max_timestamp = int(ceil(ts[-1] + (ts[-1] - ts[-2]) / 2))

        # Make the max timestamp at least 24 hours larger than the end of the
        # last interval; this is to allow forecasting into the future for a
        # few more hours.
        self.max_timestamp += 24 * 60 * 60


KpIndexData.INVALID = KpIndexData()

data_lock: Lock = Lock()
last_data: KpIndexData = KpIndexData.INVALID
data_valid_until: float = monotonic() - 1
selected_data_provider: str = ""

#: Mapping from data source names to callables that can be called with a
#: single timestamp and return the corresponding Kp-index data
data_providers: Dict[str, Callable[[], Awaitable[KpIndexData]]] = {}


async def fetch_kp_index_now() -> None:
    """Fetches and stores an up-to-date Kp-index data from the configured data
    source, updating the validity timestamp.
    """
    global data_lock, data_providers, data_valid_until, last_data, selected_data_provider

    # if another task is already updating the Kp-index, exit now and do not
    # do anything
    if data_lock.locked():
        return

    async with data_lock:  # type: ignore
        try:
            func = data_providers[selected_data_provider]
            last_data = await func()
            data_valid_until = monotonic() + 3600  # cache for 30 minutes
        except Exception:
            # There was an error; we cache it for 10 seconds and then try again
            # if the user asks again
            last_data = KpIndexData.INVALID
            data_valid_until = monotonic() + 10


async def _fetch_text_file_from_http(url: str) -> Iterable[str]:
    """Fetches a text file from an HTTP URL, following redirects appropriately
    and raising exceptions as needed.

    Returns the lines in the text file as an iterable.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.text.split("\n")


async def _fetch_kp_index_from_noaa() -> KpIndexData:
    """Fetches an up-to-date Kp-index data from the NOAA data source
    and returns it.
    """
    entries: List[Tuple[int, float]] = []

    url = "https://services.swpc.noaa.gov/text/daily-geomagnetic-indices.txt"
    lines = await _fetch_text_file_from_http(url)

    for line in lines:
        # Ignore empty, header and comment lines. Basically we only need
        # lines starting with "2" because that's the first digit of the date.
        line = line.strip()
        if not line or not line.startswith("2"):
            continue

        # Parse date, skip invalid lines
        try:
            date = datetime.strptime(line[:10], "%Y %m %d").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # Kp-index values are from char 63, 2 character per Kp-index
        values = [int(line[x : (x + 2)]) for x in range(63, 79, 2)]
        date += timedelta(hours=1.5)
        dt = timedelta(hours=3)

        for value in values:
            if value >= 0:
                entries.append((int(date.timestamp()), value))
            date += dt

    return KpIndexData.from_midpoints_and_values(entries)


async def _fetch_kp_index_from_potsdam() -> KpIndexData:
    """Fetches an up-to-date Kp-index data from the GFZ data source in Potsdam
    and returns it.
    """
    entries: List[Tuple[int, float]] = []

    url = "http://www-app3.gfz-potsdam.de/kp_index/Kp_ap_nowcast.txt"
    lines = await _fetch_text_file_from_http(url)

    for line in lines:
        # Ignore empty and comment lines
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Keep only lines that contain at least 8 columns as the Kp index
        # is in the 8th column
        parts = line.split()
        if len(parts) < 8:
            continue

        # Parse days since 1932-01-01 00:00 UTC to midpoint of interval, skip
        # invalid lines
        try:
            days_to_midpoint = float(parts[6])
        except ValueError:
            continue

        # Parse Kp index, skip invalid lines
        try:
            kp_index = float(parts[7])
        except ValueError:
            continue
        if kp_index < 0 or kp_index >= 200:
            continue

        dt = datetime(1932, 1, 1) + timedelta(days=days_to_midpoint)
        entries.append((int(dt.replace(tzinfo=timezone.utc).timestamp()), kp_index))

    return KpIndexData.from_midpoints_and_values(entries)


async def provide_kp_index(weather: Weather, position: Optional[GPSCoordinate]) -> None:
    """Extends the given weather object with the planetary Kp index estimate
    at the timestamp corresponding to the weather object.
    """
    global data_valid_until, last_data

    if getattr(weather, "kpIndex", None) is not None:
        return

    now = monotonic()
    if now > data_valid_until:
        try:
            with fail_after(1):
                await fetch_kp_index_now()
        except TooSlowError:
            # Thread was not cancelled, it is still running in the background.
            # Eventually it will also set last_data and update the timestamp,
            # but we want to return something fast
            now = monotonic()
            if now > data_valid_until:
                last_data = KpIndexData.INVALID
                data_valid_until = monotonic() + 10

    kp_index = last_data.get_kp_index_for(int(weather.timestamp))
    if kp_index is not None:
        weather.kpIndex = kp_index  # type: ignore


def set_selected_data_provider(value: Optional[str]) -> None:
    """Sets the data provider that we use to retrieve the Kp index estimate from.
    Invalidates data fetched earlier if the data provider changes.
    """
    global selected_data_provider, last_data, data_valid_until

    value = value or ""

    if selected_data_provider == value:
        return

    last_data = KpIndexData.INVALID
    data_valid_until = monotonic() - 1
    selected_data_provider = value


async def run(app, configuration):
    global selected_data_provider

    set_selected_data_provider(configuration.get("source", "potsdam"))

    id = f"kpIndex:{selected_data_provider}"

    try:
        with app.import_api("weather").use_provider(provide_kp_index, id=id):
            await sleep_forever()
    finally:
        set_selected_data_provider(None)


data_providers.update(
    noaa=_fetch_kp_index_from_noaa, potsdam=_fetch_kp_index_from_potsdam
)
dependencies = ("weather",)
description = "Weather provider that provides planetary K-index values"
schema = {
    "properties": {
        "source": {
            "type": "string",
            "title": "Data source to use",
            "default": "potsdam",
            "enum": ["potsdam", "noaa"],
            "options": {
                "enum_titles": [
                    "GFZ German Research Centre for Geosciences, Germany",
                    "NOAA Space Weather Prediction Center, USA",
                ],
            },
        }
    }
}
