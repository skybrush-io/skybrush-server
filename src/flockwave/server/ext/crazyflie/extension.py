"""Extension that adds support for Crazyflie drones."""

from contextlib import AsyncExitStack, ExitStack
from errno import EACCES
from functools import partial
from logging import Logger
from pathlib import Path
from struct import Struct
from trio import open_memory_channel, open_nursery
from typing import Any, Callable, Dict, Optional

from flockwave.connections.factory import create_connection
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose

from .connection import CrazyradioConnection
from .crtp_extensions import DRONE_SHOW_PORT, DroneShowCommand
from .driver import CrazyflieDriver
from .scanning import CrazyradioScannerTask

__all__ = ("construct", "schema")


class CrazyflieDronesExtension(UAVExtensionBase):
    """Extension that adds support for Crazyflie drones."""

    log: Logger

    _driver: CrazyflieDriver

    def _create_driver(self) -> CrazyflieDriver:
        assert self.app is not None
        return CrazyflieDriver(
            cache=Path(self.app.dirs.user_cache_dir) / "ext" / "crazyflie"
        )

    def configure_driver(self, driver, configuration: Dict[str, Any]) -> None:
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.
        """
        driver.debug = bool(configuration.get("debug", False))
        driver.fence_distance = float(configuration.get("fence_distance", 0.0))
        driver.id_format = configuration.get("id_format", "{0}")
        driver.log = self.log.getChild("driver")
        driver.status_interval = float(configuration.get("status_interval", 0.5))
        driver.use_fake_position = configuration.get("feed_fake_position", False)
        driver.use_test_mode = bool(configuration.get("testing", False))

        if driver.use_fake_position is True:
            driver.use_fake_position = (0, 0, 0)

    async def run(self, app, configuration):
        from aiocflib.crtp.drivers import init_drivers
        from aiocflib.crtp.drivers.radio import SharedCrazyradio
        from aiocflib.errors import NotFoundError

        init_drivers()

        # TODO(ntamas): we need to acquire all shared Crazyradio instances that
        # we will use _now_, otherwise Trio gets confused. This is fragile but
        # it's the best we can do.
        async with AsyncExitStack() as stack:
            num_radios = 0

            for index in range(1):
                try:
                    await stack.enter_async_context(SharedCrazyradio(index))
                    num_radios += 1
                except NotFoundError:
                    self.log.warn(f"Could not acquire Crazyradio #{index}")
                except OSError as ex:
                    if ex.errno == EACCES:
                        self.log.warn(
                            f"Permission denied while trying to access Crazyradio #{index}. Do you have the permissions to work with USB devices?"
                        )
                    else:
                        raise ex

            if not num_radios:
                self.log.error(
                    "Failed to acquire any Crazyradios; Crazyflie extension disabled.",
                    extra={"sentry_ignore": True},
                )
            else:
                return await self._run(app, configuration)

    async def _run(self, app, configuration):
        assert self.app is not None

        signals = self.app.import_api("signals")

        connection_config = configuration.get("connections", [])

        # Create a channel that will be used to create new UAVs as needed
        new_uav_tx_channel, new_uav_rx_channel = open_memory_channel(0)

        # We need a nursery that will be the parent of all tasks that handle
        # Crazyradio connections
        async with open_nursery() as nursery:
            with ExitStack() as stack:
                # Let the create_connection connection factory know about the
                # CrazyradioConnection class
                stack.enter_context(
                    create_connection.use(CrazyradioConnection, "crazyradio")
                )

                # Register all the connections and ask the app to supervise them
                for index, spec in enumerate(connection_config):
                    connection = create_connection(spec)
                    if hasattr(connection, "assign_nursery"):
                        connection.assign_nursery(nursery)

                    # Register the radio connection in the connection registry
                    stack.enter_context(
                        app.connection_registry.use(
                            connection,
                            f"Crazyradio{index}",
                            description=f"Crazyradio connection {index}",
                            purpose=ConnectionPurpose.uavRadioLink,
                        )
                    )

                    # Let the connection know when the light configuration of
                    # the show changes
                    stack.enter_context(
                        signals.use(
                            {
                                "show:countdown": partial(
                                    self._on_show_countdown_notification,
                                    connection=connection,
                                    start_soon=nursery.start_soon,
                                ),
                                "show:lights_updated": partial(
                                    self._on_show_light_configuration_changed,
                                    connection=connection,
                                ),
                            }
                        )
                    )

                    # Run a background task that scans the radio connection and
                    # attempts to find newly booted Crazyflie drones
                    task = partial(
                        CrazyradioScannerTask.create_and_run,
                        log=self.log,
                        channel=new_uav_tx_channel,
                    )
                    nursery.start_soon(partial(app.supervise, connection, task=task))

                # Wait for newly detected UAVs and spawn a task for each of them
                async with new_uav_rx_channel:
                    async for address_space, index, disposer in new_uav_rx_channel:
                        uav = self._driver.get_or_create_uav(address_space, index)

                        # uav might be None if the user hits the license limit
                        if uav:
                            nursery.start_soon(uav.run, disposer)

    def _on_show_countdown_notification(
        self,
        sender,
        delay: Optional[float],
        *,
        connection: CrazyradioConnection,
        start_soon: Callable,
    ) -> None:
        if delay is not None:
            delay = int(delay * 1000)
            if abs(delay) >= 32000:
                # Too far in the future
                delay = None

        if delay is None:
            data = Struct("<B").pack(DroneShowCommand.STOP)
        else:
            data = Struct("<Bh").pack(DroneShowCommand.START, delay)

        start_soon(connection.broadcast, DRONE_SHOW_PORT, data)

    def _on_show_light_configuration_changed(
        self, sender, config, *, connection: CrazyradioConnection
    ) -> None:
        # TODO(ntamas)
        pass


construct = CrazyflieDronesExtension
schema = {
    "properties": {
        "connections": {
            "title": "Connection URLs",
            "type": "array",
            "format": "table",
            "items": {"type": "string"},
        },
        "debug": {
            "type": "boolean",
            "title": "Debug mode",
            "format": "checkbox",
            "propertyOrder": 2000,
        },
        "fence_distance": {
            "type": "number",
            "title": "Safety fence distance, in meters",
            "minimum": 0,
            "default": 1,
            "description": (
                "Before a show, an axis-aligned safety fence is configured on "
                "each Crazyflie drone based on its own trajectory in the show. "
                "The motors of the drone are shut down if the drone crosses the "
                "safety fence. This setting specifies the distance between the "
                "bounding box of the trajectory and the safety fence. Recommended "
                "setting is at least 1 meter for Lighthouse positioning and at "
                "least 2 meters for UWB positioning. Untick the checkbox or "
                "set the value to zero to turn off the safety fence."
            ),
            "propertyOrder": 1500,
            "required": False,
        },
        "id_format": {
            "type": "string",
            "default": "{0}",
            "title": "ID format",
            "description": "Python format string that determines the format of the IDs of the drones created by this extension.",
        },
        "status_interval": {
            "type": "number",
            "minimum": 0.1,
            "default": 0.5,
            "title": "Interval between status packets",
            "description": "Length of the time interval between two consecutive attempts to retrieve status information from a Crazyflie show drone. E.g., 0.5 = 0.5 seconds = two status reports per second.",
        },
        "testing": {
            "type": "boolean",
            "title": "Testing mode",
            "description": "Tick this checkbox to prevent the Crazyflie drones from starting their motors while testing a show file",
            "format": "checkbox",
            "propertyOrder": 2000,
        },
    }
}
