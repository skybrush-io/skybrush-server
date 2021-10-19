from time import time
from typing import Awaitable, Callable, Optional, Union

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.model.metamagic import ModelMeta
from flockwave.spec.schema import get_complex_object_schema

__all__ = ("Weather", "WeatherProvider")


class Weather(metaclass=ModelMeta):
    """Class representing all that the server knows about the weather at a given
    geographical location and time.
    """

    class __meta__:
        schema = get_complex_object_schema("weather")

    def __init__(
        self, position: Optional[GPSCoordinate] = None, timestamp: Optional[int] = None
    ):
        self.position = position
        self.timestamp = timestamp if timestamp is not None else time()


#: Type specification for synchronous weather provider functions
SyncWeatherProvider = Callable[[Weather, GPSCoordinate], None]

#: Type specification for asynchronous weather provider functions
AsyncWeatherProvider = Callable[[Weather, GPSCoordinate], Awaitable[None]]

#: Type specification for weather provider functions
WeatherProvider = Callable[
    [Weather, Optional[GPSCoordinate]], Union[None, Awaitable[None]]
]
