from colour import Color
from functools import partial
from random import uniform
from trio import open_nursery, sleep, sleep_forever
from typing import Callable

from flockwave.gps.vectors import (
    FlatEarthCoordinate,
    FlatEarthToGPSCoordinateTransformation,
)
from flockwave.spec.ids import make_valid_object_id
from flockwave.server.registries.errors import RegistryFull

from ..base import UAVExtension

from .driver import VirtualUAV, VirtualUAVDriver
from .placement import place_drones


__all__ = ("construct", "dependencies")


class VirtualUAVProviderExtension(UAVExtension):
    """Extension that creates one or more virtual UAVs in the server.

    Virtual UAVs circle around a given point in a given radius, with constant
    angular velocity. They are able to respond to landing and takeoff
    requests, and also handle the following commands:

    * Sending ``yo`` to a UAV makes it respond with either ``yo!``, ``yo?``
      or ``yo.``, with a mean delay of 500 milliseconds.

    * Sending ``timeout`` to a UAV makes it register the command but never
      finish its execution. Useful for testing the timeout and cancellation
      mechanism of the command execution manager of the server.
    """

    _driver: VirtualUAVDriver

    def __init__(self):
        """Constructor."""
        super().__init__()
        self._delay = 1

        self.radiation = None
        self.uavs = []
        self.uavs_armed_after_boot = False
        self.uav_ids = []

    def _create_driver(self):
        return VirtualUAVDriver()

    def configure(self, configuration):
        super().configure(configuration)

        # Get the number of UAVs to create and the format of the IDs
        count = configuration.get("count", 0)
        id_format = configuration.get("id_format", "VIRT-{0}")

        # Specify the default takeoff area
        default_takeoff_area = {"type": "grid", "spacing": 5}

        # Set the status updater thread frequency
        self.delay = configuration.get("delay", 1)

        # Get the center of the home positions
        if "origin" not in configuration and "center" in configuration:
            if self.log:
                self.log.warn("'center' is deprecated; use 'origin' instead")
            configuration["origin"] = configuration.pop("center")

        # Create a transformation from flat Earth to GPS
        origin_amsl = (
            configuration["origin"][2] if len(configuration["origin"]) > 2 else None
        )
        coordinate_system = {
            "origin": configuration["origin"][:2],
            "orientation": configuration.get("orientation", 0),
            "type": configuration.get("type", "nwu"),
        }
        trans = FlatEarthToGPSCoordinateTransformation.from_json(coordinate_system)

        # Place the given number of drones
        home_positions = [
            FlatEarthCoordinate(x=vec.x, y=vec.y, amsl=origin_amsl, agl=0)
            for vec in place_drones(
                count, **configuration.get("takeoff_area", default_takeoff_area)
            )
        ]

        # add stochasticity to positions and headings if needed
        if configuration.get("add_noise", False):
            # define noise levels here
            position_noise = 0.2
            heading_noise = 3
            # add noise to positions
            home_positions = [
                FlatEarthCoordinate(
                    x=p.x + uniform(-position_noise, position_noise),
                    y=p.y + uniform(-position_noise, position_noise),
                    # TODO: add amsl noise if we can be sure that amsl is not None
                    amsl=p.amsl,  # + uniform(-position_noise, position_noise),
                    agl=p.agl,
                )
                for p in home_positions
            ]
            # add noise to headings
            headings = [
                (trans.orientation + uniform(-heading_noise, heading_noise)) % 360
                for p in home_positions
            ]
        else:
            headings = [trans.orientation] * len(home_positions)

        # Generate IDs for the UAVs and then create them
        self.uav_ids = [
            make_valid_object_id(id_format.format(index)) for index in range(count)
        ]
        self.uavs = [
            self._driver.create_uav(id, home=trans.to_gps(home), heading=heading)
            for id, home, heading in zip(self.uav_ids, home_positions, headings)
        ]

        # Get hold of the 'radiation' extension and associate it to all our
        # UAVs
        try:
            radiation_ext = self.app.extension_manager.import_api("radiation")
        except Exception:
            radiation_ext = None
        for uav in self.uavs:
            uav.radiation_ext = radiation_ext

    def configure_driver(self, driver, configuration):
        # Set whether the virtual drones should be armed after boot
        driver.uavs_armed_after_boot = bool(configuration.get("arm_after_boot"))
        driver.use_battery_percentages = bool(
            configuration.get("use_battery_percentages", True)
        )

    @property
    def delay(self):
        """Number of seconds that must pass between two consecutive
        simulated status updates to the UAVs.
        """
        return self._delay

    @delay.setter
    def delay(self, value):
        self._delay = max(float(value), 0)

    async def simulate_uav(self, uav: VirtualUAV, spawn: Callable):
        """Simulates the behaviour of a single UAV in the application.

        Parameters:
            uav: the virtual UAV to simulate
            spawn: function to call when the UAV wishes to spawn a background
                task
        """
        try:
            await self._simulate_uav(uav, spawn)
        except RegistryFull:
            # This is okay
            pass

    async def _simulate_uav(self, uav: VirtualUAV, spawn: Callable):
        assert self.app is not None

        updater = partial(self.app.request_to_send_UAV_INF_message_for, [uav.id])

        with self.app.object_registry.use(uav):
            while True:
                # Simulate the UAV behaviour from boot time
                shutdown_reason = await uav.run_single_boot(
                    self._delay,
                    mutate=self.create_device_tree_mutation_context,
                    notify=updater,
                    spawn=spawn,
                )

                # If we need to restart, let's restart after a short delay.
                # Otherwise let's stop the loop.
                if shutdown_reason == "shutdown":
                    break
                else:
                    await sleep(0.2)

    async def run(self):
        assert self.app is not None

        signals = self.app.import_api("signals")
        with signals.use({"show:lights_updated": self._on_lights_updated}):
            await sleep_forever()

    async def worker(self, app, configuration, logger):
        """Main background task of the extension that updates the state of
        the UAVs periodically.
        """
        async with open_nursery() as nursery:
            for uav in self.uavs:
                nursery.start_soon(self.simulate_uav, uav, nursery.start_soon)

    def _on_lights_updated(self, sender, config):
        color = config.color if str(config.effect.value) == "solid" else None
        if color is not None:
            color = Color(rgb=(x / 255.0 for x in color))

        for uav in self.uavs:
            uav.set_led_color(color)


construct = VirtualUAVProviderExtension
dependencies = ("signals",)
description = "Simulated, non-realistic UAVs for testing or demonstration purposes"
