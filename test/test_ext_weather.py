from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock

from flockwave.gps.vectors import GPSCoordinate
from pytest import fixture, raises

from flockwave.server.ext import weather as weather_ext
from flockwave.server.ext.magnetic_field import provide_magnetic_field
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.weather import (
    PositionNotSupportedError,
    Weather,
    WeatherProvider,
    WeatherProviderError,
)

from .recording_message_hub import RecordingMessageHub

POSITION = [474979000, 190402000]


@fixture
def client() -> Client:
    return Client(_id="alice", _channel=MagicMock())


@fixture
def providers() -> Iterator[ExitStack]:
    """Exit stack for registering weather providers; unregisters all of them
    at the end of the test.
    """
    with ExitStack() as stack:
        yield stack


def add_provider(
    stack: ExitStack, provider: WeatherProvider, id: str, priority: int = 0
) -> None:
    stack.enter_context(weather_ext.registry.use(provider, id=id, priority=priority))


def message(hub: RecordingMessageHub, body: dict[str, Any]) -> FlockwaveMessage:
    return hub._message_builder.create_message(body)


def body_of(response: Any) -> dict[str, Any]:
    if isinstance(response, FlockwaveResponse):
        return dict(response.body)
    assert isinstance(response, dict)
    return response


def set_temperature(weather: Weather, position: GPSCoordinate | None) -> None:
    weather.temperature = 2840  # type: ignore


async def set_wind_speed(weather: Weather, position: GPSCoordinate | None) -> None:
    weather.windSpeed = 6000  # type: ignore


def require_position(weather: Weather, position: GPSCoordinate | None) -> None:
    raise PositionNotSupportedError("Position required")


def fail_with_provider_error(weather: Weather, position: GPSCoordinate | None) -> None:
    raise WeatherProviderError("Service unavailable")


def fail_unexpectedly(weather: Weather, position: GPSCoordinate | None) -> None:
    raise ValueError("bug")


async def test_wth_at_skips_providers_not_supporting_position(
    hub: RecordingMessageHub, client: Client, providers: ExitStack
) -> None:
    add_provider(providers, set_temperature, "temperature", priority=1)
    add_provider(providers, require_position, "station")
    add_provider(providers, set_wind_speed, "wind", priority=-1)

    response = await weather_ext.handle_WTH_AT(
        message(hub, {"type": "WTH-AT", "position": POSITION}), client, hub
    )

    weather = body_of(response)["weather"].json
    assert weather["temperature"] == 2840
    assert weather["windSpeed"] == 6000


async def test_wth_at_fails_with_first_provider_error(
    hub: RecordingMessageHub, client: Client, providers: ExitStack
) -> None:
    calls = []

    def record_call(weather: Weather, position: GPSCoordinate | None) -> None:
        calls.append(position)

    add_provider(providers, set_temperature, "temperature", priority=2)
    add_provider(providers, fail_with_provider_error, "remote", priority=1)
    add_provider(providers, fail_unexpectedly, "buggy")
    add_provider(providers, record_call, "recorder", priority=-1)

    response = await weather_ext.handle_WTH_AT(
        message(hub, {"type": "WTH-AT", "position": POSITION}), client, hub
    )

    body = body_of(response)
    assert body["type"] == "ACK-NAK"
    assert body["reason"] == "Service unavailable"
    assert not calls


async def test_wth_at_reports_unexpected_errors(
    hub: RecordingMessageHub, client: Client, providers: ExitStack
) -> None:
    add_provider(providers, fail_unexpectedly, "buggy")

    response = await weather_ext.handle_WTH_AT(
        message(hub, {"type": "WTH-AT", "position": POSITION}), client, hub
    )

    body = body_of(response)
    assert body["type"] == "ACK-NAK"
    assert body["reason"] == "bug"


async def test_wth_inf_reports_provider_errors_per_id(
    hub: RecordingMessageHub, client: Client, providers: ExitStack
) -> None:
    add_provider(providers, set_wind_speed, "wind")
    add_provider(providers, require_position, "openweather")
    add_provider(providers, fail_unexpectedly, "buggy")

    response = await weather_ext.handle_WTH_INF(
        message(
            hub,
            {"type": "WTH-INF", "ids": ["wind", "openweather", "buggy", "spam"]},
        ),
        client,
        hub,
    )

    body = body_of(response)
    assert list(body["status"]) == ["wind"]
    assert body["status"]["wind"].json["windSpeed"] == 6000
    assert body["error"] == {
        "openweather": "Position required",
        "buggy": "bug",
        "spam": "No such weather provider",
    }


async def test_wth_list_lists_all_providers(
    hub: RecordingMessageHub, client: Client, providers: ExitStack
) -> None:
    add_provider(providers, set_wind_speed, "wind")
    add_provider(providers, require_position, "openweather")

    response = weather_ext.handle_WTH_LIST(
        message(hub, {"type": "WTH-LIST"}), client, hub
    )

    assert sorted(body_of(response)["ids"]) == ["openweather", "wind"]


async def test_magnetic_field_requires_position() -> None:
    with raises(PositionNotSupportedError):
        await provide_magnetic_field(Weather(), None)
