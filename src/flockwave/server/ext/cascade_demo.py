"""Experimental extension to demonstrate the connection between an ERP system
and a Skybrush server, in collaboration with Cascade Ltd
"""

from collections import defaultdict
from dataclasses import dataclass
from time import time
from trio import sleep_forever
from typing import Dict, List, Tuple

from flockwave.gps.vectors import GPSCoordinate

from .base import ExtensionBase
from .dock.model import Dock


@dataclass
class Station:
    """Model object representing a single station in the demo."""

    id: str
    position: GPSCoordinate

    @classmethod
    def from_json(cls, obj: Tuple[float, float], id: str):
        """Creates a station from its JSON representation."""
        pos = GPSCoordinate(lon=obj[0], lat=obj[1], agl=0)
        return cls(id=id, position=pos)

    def create_dock(self) -> Dock:
        """Creates a docking station object from this specification."""
        dock = Dock(id=self.id)
        dock.update_status(position=self.position)
        return dock


@dataclass
class Trip:
    """Model object representing a single scheduled trip of a UAV in the demo."""

    uav_id: str
    start_time: float
    route: List[str]


class ERPSystemConnectionDemoExtension(ExtensionBase):
    """Experimental extension to demonstrate the connection between an ERP system
    and a Skybrush server, in collaboration with Cascade Ltd
    """

    def __init__(self):
        super().__init__()

        self._stations = []
        self._trips = defaultdict(Trip)

    def configure(self, configuration):
        super().configure(configuration)
        self.configure_stations(configuration.get("stations"))

    def configure_stations(self, stations: Dict[str, Dict]):
        """Parses the list of stations from the configuration file so they
        can be added as docks later.
        """
        stations = stations or {}
        station_ids = sorted(stations.keys())
        self._stations = [
            Station.from_json(stations[station_id], id=station_id)
            for station_id in station_ids
        ]

        if self._stations:
            self.log.info(f"Loaded {len(self._stations)} stations.")

    def handle_trip_addition(self, message, sender, hub):
        """Handles the addition of a new trip to the list of scheduled trips."""
        uav_id = message.body.get("uavId")
        if not isinstance(uav_id, str):
            return hub.reject(message, "Missing UAV ID or it is not a string")

        start_time_ms = message.body.get("startTime")
        try:
            start_time_ms = int(start_time_ms)
        except Exception:
            pass
        if not isinstance(start_time_ms, int):
            return hub.reject(message, "Missing start time or it is not an integer")

        start_time_sec = start_time_ms / 1000
        if start_time_sec < time():
            return hub.reject(message, "Start time is in the past")

        route = message.body.get("route")
        if not isinstance(route, list) or not route:
            return hub.reject(message, "Route is not specified or is empty")

        if any(not isinstance(station, str) for station in route):
            return hub.reject(message, "Station names in route must be strings")

        self._trips[uav_id] = Trip(
            uav_id=uav_id, start_time=start_time_sec, route=route
        )

        return hub.acknowledge(message)

    def handle_trip_cancellation(self, message, sender, hub):
        """Cancels the current trip on a given drone."""
        uav_id = message.body.get("uavId")
        if not isinstance(uav_id, str):
            return hub.reject(message, "Missing UAV ID or it is not a string")

        trip = self._trips.pop(uav_id, None)
        if trip is None:
            return hub.reject(message, "UAV has no scheduled trip")

        return hub.acknowledge(message)

    async def run(self):
        handlers = {
            "X-TRIP-ADD": self.handle_trip_addition,
            "X-TRIP-CANCEL": self.handle_trip_cancellation,
        }

        docks = [station.create_dock() for station in self._stations]

        with self.app.message_hub.use_message_handlers(handlers):
            with self.app.object_registry.use(*docks):
                await sleep_forever()


construct = ERPSystemConnectionDemoExtension
dependencies = ("dock",)
