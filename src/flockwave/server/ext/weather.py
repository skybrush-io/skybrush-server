"""Extension that provides weather station related commands to the server, and
allow weather station providers to register themselves.
"""

from inspect import isawaitable
from trio import sleep_forever

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.message_hub import MessageHub
from flockwave.server.model.client import Client
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.weather import Weather
from flockwave.server.registries import find_in_registry, WeatherProviderRegistry

#: Registry containing the registered weather providers by ID
registry = WeatherProviderRegistry()


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
        # TODO(ntamas): use priorities and ordering!
        for provider in registry:
            result = provider(weather, gps_coordinate)
            if isawaitable(result):
                await result
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
            result = provider(weather, None)
            if isawaitable(result):
                await result
            statuses[station_id] = weather

    return response


def handle_WTH_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"ids": list(registry.ids)}


async def run(app):
    """Unloads the extension."""
    with app.message_hub.use_message_handlers(
        {
            "WTH-AT": handle_WTH_AT,
            "WTH-INF": handle_WTH_INF,
            "WTH-LIST": handle_WTH_LIST,
        }
    ):
        await sleep_forever()


description = "Basic support for weather stations"
exports = {"add_provider": registry.add, "use_provider": registry.use}
schema = {}
