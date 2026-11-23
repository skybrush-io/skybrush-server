from collections.abc import Awaitable, Callable
from time import time

from flockwave.gps.vectors import GPSCoordinate
from flockwave.spec.schema import get_complex_object_schema

from flockwave.server.model.metamagic import ModelMeta

__all__ = (
    "PositionNotSupportedError",
    "Weather",
    "WeatherProvider",
    "WeatherProviderError",
)


class Weather(metaclass=ModelMeta):
    """Class representing all that the server knows about the weather at a given
    geographical location and time.
    """

    class __meta__:
        schema = get_complex_object_schema("weather")

    def __init__(
        self, position: GPSCoordinate | None = None, timestamp: int | None = None
    ):
        self.position = position
        self.timestamp = timestamp if timestamp is not None else time()


SyncWeatherProvider = Callable[[Weather, GPSCoordinate], None]
"""Type specification for synchronous weather provider functions."""

AsyncWeatherProvider = Callable[[Weather, GPSCoordinate], Awaitable[None]]
"""Type specification for asynchronous weather provider functions."""

WeatherProvider = Callable[[Weather, GPSCoordinate | None], None | Awaitable[None]]
"""Type specification for weather provider functions.

The position is ``None`` when a client asks for the status of the provider
itself and not for the weather at a given location.
"""


class WeatherProviderError(RuntimeError):
    """Base class for errors raised by weather providers.

    The message of the error is sent to the client that requested the weather
    information.
    """

    def __init__(self, message: str | None = None):
        """Constructor.

        Args:
            message: the error message
        """
        super().__init__(message or "Weather provider error")


class PositionNotSupportedError(WeatherProviderError):
    """Error raised by a weather provider when it cannot provide weather
    information for the requested position.

    Providers covering a limited area raise this error for positions outside
    that area. Providers whose data depends on the location raise it when they
    are called without a position.

    Weather requests at a given position skip providers that raise this error.
    """

    def __init__(self, message: str | None = None):
        """Constructor.

        Args:
            message: the error message
        """
        super().__init__(message or "Position not supported by weather provider")
