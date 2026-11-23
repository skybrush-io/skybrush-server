"""Extension that provides weather station related commands to the server, and
allow weather station providers to register themselves.
"""

from inspect import isawaitable
from logging import Logger

from flockwave.gps.vectors import GPSCoordinate
from trio import sleep_forever

from flockwave.server.message_hub import MessageHub
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.weather import (
    PositionNotSupportedError,
    Weather,
    WeatherProvider,
    WeatherProviderError,
)
from flockwave.server.registries import WeatherProviderRegistry, find_in_registry

log: Logger | None = None
"""Logger of the extension; used to log unexpected errors from providers."""

registry = WeatherProviderRegistry()
"""Registry containing the registered weather providers by ID."""


async def _call_provider(
    provider: WeatherProvider, weather: Weather, position: GPSCoordinate | None
) -> None:
    """Calls a weather provider, awaiting its result if needed.

    Unexpected exceptions are logged with a traceback and then re-raised.
    Errors derived from `WeatherProviderError` are re-raised without logging
    because they are expected and are reported to the client.
    """
    try:
        result = provider(weather, position)
        if isawaitable(result):
            await result
    except WeatherProviderError:
        raise
    except Exception:
        if log:
            log.exception(f"Unexpected error from weather provider {provider!r}")
        raise


async def handle_WTH_AT(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    body = {}
    position = message.body.get("position")
    if not isinstance(position, list) or len(position) < 2:
        return hub.acknowledge(
            message, outcome=False, reason="Invalid GPS coordinate provided"
        )

    if registry.num_entries > 0:
        gps_coordinate = GPSCoordinate.from_json(position)
        weather = Weather(position=gps_coordinate)
        for provider in registry.iter_providers_by_priority():
            try:
                await _call_provider(provider, weather, gps_coordinate)
            except PositionNotSupportedError:
                continue
            except Exception as ex:
                return hub.acknowledge(
                    message, outcome=False, reason=str(ex) or type(ex).__name__
                )
        body["weather"] = weather

    return body


async def handle_WTH_INF(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
) -> FlockwaveResponse:
    statuses = {}

    body = {"status": statuses, "type": "WTH-INF"}
    response = hub.create_response_or_notification(body=body, in_response_to=message)

    for station_id in message.get_ids():
        provider = find_in_registry(
            registry,
            station_id,
            response=response,
            failure_reason="No such weather provider",
        )
        if provider:
            weather = Weather()
            try:
                await _call_provider(provider, weather, None)
            except Exception as ex:
                response.add_error(station_id, str(ex) or type(ex).__name__)
            else:
                statuses[station_id] = weather

    return response


def handle_WTH_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"ids": list(registry.ids)}


async def run(app, configuration, logger: Logger):
    """Runs the extension."""
    global log

    log = logger
    try:
        with app.message_hub.use_message_handlers(
            {
                "WTH-AT": handle_WTH_AT,
                "WTH-INF": handle_WTH_INF,
                "WTH-LIST": handle_WTH_LIST,
            }
        ):
            await sleep_forever()
    finally:
        log = None


description = "Basic support for weather stations"
exports = {"add_provider": registry.add, "use_provider": registry.use}
schema = {}
