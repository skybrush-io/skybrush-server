"""Extension that adds support for Crazyflie drones."""

from contextlib import AsyncExitStack, ExitStack
from errno import EACCES
from functools import partial
from logging import Logger
from struct import Struct
from trio import open_memory_channel, open_nursery
from trio.abc import ReceiveChannel, SendChannel
from typing import Any, Optional, TYPE_CHECKING

from flockwave.connections.factory import create_connection
from flockwave.server.ext.base import UAVExtension
from flockwave.server.model import ConnectionPurpose

from .connection import BroadcasterFunction, CrazyradioConnection
from .crtp_extensions import DRONE_SHOW_PORT, DroneShowCommand, FenceAction
from .driver import CrazyflieDriver
from .fence import FenceConfiguration
from .led_lights import CrazyflieLEDLightConfigurationManager, LightConfiguration
from .mocap import CrazyflieMocapFrameHandler
from .scanning import CrazyradioScannerTask, ScannerTaskEvent
from .types import ControllerType

if TYPE_CHECKING:
    from flockwave.server.ext.motion_capture import MotionCaptureFrame


__all__ = ("construct", "schema")


class CrazyflieDronesExtension(UAVExtension[CrazyflieDriver]):
    """Extension that adds support for Crazyflie drones."""

    log: Logger

    _driver: CrazyflieDriver

    def _create_driver(self) -> CrazyflieDriver:
        assert self.app is not None
        return CrazyflieDriver(cache=self.get_cache_dir())

    def configure_driver(
        self, driver: CrazyflieDriver, configuration: dict[str, Any]
    ) -> None:
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.
        """
        driver.debug = bool(configuration.get("debug", False))
        driver.fence_config = FenceConfiguration.from_json(configuration.get("fence"))
        driver.id_format = configuration.get("id_format", "{0}")
        driver.log = self.log
        driver.status_interval = float(configuration.get("status_interval", 0.5))
        driver.takeoff_altitude = float(configuration.get("takeoff_altitude", 1.0))
        driver.use_test_mode = bool(configuration.get("testing", False))

        controller_spec = configuration.get("controller")
        try:
            preferred_controller = ControllerType.from_json(controller_spec)
        except ValueError:
            self.log.warn(
                f"Unknown preferred controller in configuration: {controller_spec!r}"
            )
        else:
            driver.preferred_controller = preferred_controller

    async def run(self, app, configuration):
        from aiocflib.crtp.drivers import init_drivers
        from aiocflib.crtp.drivers.radio import SharedCrazyradio
        from aiocflib.errors import NotFoundError

        init_drivers()

        connection_config = configuration.get("connections", [])
        radio_indices: list[int] = []
        for spec in connection_config:
            index = CrazyradioConnection.parse_radio_index_from_uri(spec)
            if index is not None:
                radio_indices.append(index)

        # TODO(ntamas): we need to acquire all shared Crazyradio instances that
        # we will use _now_, otherwise Trio gets confused. This is fragile but
        # it's the best we can do.
        async with AsyncExitStack() as stack:
            num_radios = 0

            for index in radio_indices:
                try:
                    await stack.enter_async_context(SharedCrazyradio(index))
                    num_radios += 1
                except NotFoundError:
                    self.log.warning(f"Could not acquire Crazyradio #{index}")
                except OSError as ex:
                    if ex.errno == EACCES:
                        self.log.warning(
                            f"Permission denied while trying to access Crazyradio #{index}. Do you have the permissions to work with USB devices?"
                        )
                    else:
                        raise ex

            if num_radios < len(radio_indices):
                if not num_radios:
                    self.log.error(
                        "Failed to acquire any Crazyradios.",
                        extra={"telemetry": "ignore"},
                    )
                else:
                    self.log.error(
                        f"Requested {len(radio_indices)} Crazyradios but only {num_radios} were acquired.",
                        extra={"telemetry": "ignore"},
                    )

            return await self._run(app, configuration)

    async def _run(self, app, configuration):
        assert self.app is not None

        signals = self.app.import_api("signals")

        connection_config = configuration.get("connections", [])

        # Create a channel that will be used to create new UAVs as needed
        new_uav_rx_channel: ReceiveChannel[ScannerTaskEvent]
        new_uav_tx_channel: SendChannel[ScannerTaskEvent]
        new_uav_tx_channel, new_uav_rx_channel = open_memory_channel(0)

        # We need a nursery that will be the parent of all tasks that handle
        # Crazyradio connections
        async with open_nursery() as nursery:
            with ExitStack() as stack:
                # Let the create_connection connection factory know about the
                # CrazyradioConnection class
                stack.enter_context(
                    create_connection.use(
                        CrazyradioConnection, CrazyradioConnection.SCHEME
                    )
                )

                # Register all the connections and ask the app to supervise them
                num_connections = len(connection_config)
                for index, spec in enumerate(connection_config):
                    connection = create_connection(spec)
                    if hasattr(connection, "assign_nursery"):
                        connection.assign_nursery(nursery)

                    # Create a function that enqueues a packet for broadcasting
                    # over the given connection
                    broadcaster = partial(nursery.start_soon, connection.broadcast)

                    # Create a dedicated LED manager for the connection
                    led_manager = CrazyflieLEDLightConfigurationManager(broadcaster)

                    # Create a dedicated mocap frame handler for the connection
                    mocap_frame_handler = CrazyflieMocapFrameHandler(
                        self._driver, broadcaster
                    )

                    # Register the radio connection in the connection registry
                    stack.enter_context(
                        app.connection_registry.use(
                            connection,
                            f"Crazyradio{index}",
                            description=f"Crazyradio connection {index}",
                            purpose=ConnectionPurpose.uavRadioLink,  # type: ignore
                        )
                    )

                    # Subscribe to the signals that we are interested in:
                    # mocap frames, show countdown and LED configuration changes
                    stack.enter_context(
                        signals.use(
                            {
                                "motion_capture:frame": partial(
                                    self._on_motion_capture_frame_received,
                                    handler=mocap_frame_handler,
                                ),
                                "show:countdown": partial(
                                    self._on_show_countdown_notification,
                                    broadcaster=broadcaster,
                                ),
                                "show:lights_updated": partial(
                                    self._on_show_light_configuration_changed,
                                    led_manager=led_manager,
                                ),
                            }
                        )
                    )

                    # Run a background task that manages the LED lights of the
                    # connected drones
                    nursery.start_soon(led_manager.run)

                    # Run a background task that scans the radio connection and
                    # attempts to find newly booted Crazyflie drones
                    task = partial(
                        CrazyradioScannerTask.create_and_run,
                        log=self.log,
                        channel=new_uav_tx_channel,
                        initial_delay=(index / num_connections),
                    )
                    nursery.start_soon(partial(app.supervise, connection, task=task))

                # Wait for newly detected UAVs and spawn a task for each of them
                async with new_uav_rx_channel:
                    async for (
                        address_space,
                        address_index,
                        disposer,
                    ) in new_uav_rx_channel:
                        uav = self._driver.get_or_create_uav(
                            address_space, address_index
                        )

                        # uav might be None if the user hits the license limit
                        if uav:
                            nursery.start_soon(uav.run, disposer)

    def _on_motion_capture_frame_received(
        self,
        sender,
        *,
        frame: "MotionCaptureFrame",
        handler: CrazyflieMocapFrameHandler,
    ) -> None:
        handler.notify_frame(frame)

    def _on_show_countdown_notification(
        self,
        sender,
        delay: Optional[float],
        *,
        broadcaster: BroadcasterFunction,
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

        broadcaster(DRONE_SHOW_PORT, 0, data)

    def _on_show_light_configuration_changed(
        self,
        sender,
        config: LightConfiguration,
        *,
        led_manager: CrazyflieLEDLightConfigurationManager,
    ) -> None:
        """Handler that is called when the user changes the LED light configuration
        of the drones in the `show` extesion.
        """
        # Make a copy of the configuration in case someone else who comes after
        # us in the handler chain messes with it
        config = config.clone()

        # Send the configuration to the driver to handle it
        led_manager.notify_config_changed(config)


construct = CrazyflieDronesExtension
schema = {
    "properties": {
        "connections": {
            "title": "Connection URLs",
            "type": "array",
            "format": "table",
            "items": {"type": "string"},
        },
        "controller": {
            "title": "Controller type",
            "type": "string",
            "default": "none",
            "enum": ["none", "auto", "pid", "mellinger", "indi", "brescianini"],
            "options": {
                "enum_titles": [
                    "Do not change the settings on the drone",
                    "Let the firmware select automatically",
                    "Use PID controller",
                    "Use Mellinger controller",
                    "Use INDI controller",
                    "Use Brescianini controller",
                ],
            },
            "description": (
                "Specifies the controller to select on the drone after a show "
                "upload. This is an advanced option."
            ),
            "propertyOrder": 2000,
        },
        "debug": {
            "type": "boolean",
            "title": "Debug mode",
            "format": "checkbox",
            "propertyOrder": 2000,
        },
        "fence": {
            "type": "object",
            "title": "Safety fence",
            "description": (
                "Before a show, an axis-aligned safety fence can optionally "
                "be configured on each Crazyflie drone based on its own "
                "trajectory in the show, and a safety action may be taken by "
                "the drone when it detects that the fence has been breached."
            ),
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "title": "Enabled",
                    "default": True,
                    "format": "checkbox",
                    "propertyOrder": 10,
                },
                "distance": {
                    "type": "number",
                    "title": "Safety fence distance, in meters",
                    "minimum": 0,
                    "default": 1,
                    "description": (
                        "The distance between the bounding box of the trajectory "
                        "and the safety fence. Recommended setting is at least 1 "
                        "meter for Lighthouse positioning and at least 2 meters "
                        "for UWB positioning. Zero or negative values turn off "
                        "the safety fence even if it is otherwise enabled above."
                    ),
                    "propertyOrder": 1500,
                },
                "action": {
                    "type": "string",
                    "title": "Action taken when fence is breached",
                    "enum": FenceAction.get_valid_string_values_in_config_schema(),
                    "options": {
                        "enum_titles": [action.describe() for action in FenceAction]
                    },
                    "default": "none",
                },
            },
        },
        "id_format": {
            "type": "string",
            "default": "{0}",
            "title": "ID format",
            "description": (
                "Python format string that determines the format of the IDs of "
                "the drones created by this extension."
            ),
        },
        "status_interval": {
            "type": "number",
            "minimum": 0.1,
            "default": 0.5,
            "title": "Interval between status packets",
            "description": (
                "Length of the time interval between two consecutive attempts "
                "to retrieve status information from a Crazyflie show drone. "
                "E.g., 0.5 = 0.5 seconds = two status reports per second."
            ),
        },
        "takeoff_altitude": {
            "type": "number",
            "minimum": 0.1,
            "default": 1.0,
            "title": "Takeoff altitude",
            "description": (
                "Altitude that a drone should take off to when receiving a "
                "takeoff command without a specified altitude, in meters."
            ),
        },
        "testing": {
            "type": "boolean",
            "title": "Testing mode",
            "description": (
                "Tick this checkbox to prevent the Crazyflie drones from "
                "starting their motors while testing a show file"
            ),
            "format": "checkbox",
            "propertyOrder": 2000,
        },
    }
}
