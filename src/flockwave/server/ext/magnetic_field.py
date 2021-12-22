"""Extension that registers a weather provider that provides information about
Earth's magnetic field using the IGRF13 model.
"""

from datetime import datetime
from trio import sleep_forever
from typing import Optional, Tuple

from igrf_model import DateBoundIGRFModel, IGRFModel

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.weather import Weather


last_model: Optional[DateBoundIGRFModel] = None
"""The last model that the extension used."""

model_validity_range: Tuple[float, float] = (-1, -1)
"""Tuple storing the POSIX timestamp range when the model is considered valid."""

ONE_WEEK = 7 * 24 * 3600
"""One week in seconds."""


async def provide_magnetic_field(
    weather: Weather, position: Optional[GPSCoordinate]
) -> None:
    """Extends the given weather object with Earth's magnetic field according
    to the IGRF13 model, at the timestamp corresponding to the weather object.
    """
    global last_model, model_validity_range

    if getattr(weather, "magneticVector", None) is not None:
        return

    if position is None:
        return

    model_valid_from, model_valid_until = model_validity_range
    timestamp = int(weather.timestamp)
    if (
        last_model is None
        or timestamp < model_valid_from
        or timestamp > model_valid_until
    ):
        last_model = IGRFModel.get(version=13).at(datetime.fromtimestamp(timestamp))

        # We assume that the magnetic field does not change significantly in
        # two weeks
        model_validity_range = timestamp - ONE_WEEK, timestamp + ONE_WEEK

    vec = last_model.evaluate(
        position.lat, position.lon, position.amsl if position.amsl is not None else 0
    )
    weather.magneticVector = (vec.north, vec.east, vec.down)  # type: ignore


async def run(app):
    id = "magneticField:igrf13"
    with app.import_api("weather").use_provider(provide_magnetic_field, id=id):
        await sleep_forever()


dependencies = ("weather",)
description = "Weather provider that provides information about Earth's magnetic field based on the IGRF13 model"
schema = {}
