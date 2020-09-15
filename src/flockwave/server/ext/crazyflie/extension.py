"""Extension that adds support for Crazyflie drones."""

from contextlib import ExitStack
from functools import partial
from trio import open_memory_channel, open_nursery, sleep_forever
from typing import Any, Dict

from flockwave.connections.factory import create_connection
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose

from .connection import CrazyradioConnection
from .driver import CrazyflieDriver
from .scanning import CrazyradioScannerTask

__all__ = ("construct",)


class CrazyflieDronesExtension(UAVExtensionBase):
    """Extension that adds support for Crazyflie drones."""

    def _create_driver(self):
        return CrazyflieDriver()

    def configure_driver(self, driver, configuration: Dict[str, Any]) -> None:
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.
        """
        driver.id_format = configuration.get("id_format", "{0:02}")
        driver.log = self.log.getChild("driver")

    async def run(self, app, configuration):
        from aiocflib.crtp.drivers import init_drivers
        from aiocflib.crtp.drivers.radio import SharedCrazyradio

        init_drivers()

        debug = bool(configuration.get("debug", False))
        connection_config = configuration.get("connections", [])

        # Create a channel that will be used to create new UAVs as needed
        new_uav_tx_channel, new_uav_rx_channel = open_memory_channel(0)

        # TODO(ntamas): we need to acquire all shared crazyradio instances that
        # we will use _now_, otherwise Trio gets confused. This is fragile but
        # it's the best we can do.
        async with SharedCrazyradio(0):
            # We need a nursery that will be the parent of all tasks that handle
            # Crazyradio connections
            async with open_nursery() as nursery:
                with ExitStack() as stack:
                    stack.enter_context(
                        create_connection.use(CrazyradioConnection, "crazyradio")
                    )

                    # Register all the connections and ask the app to supervise them
                    for index, spec in enumerate(connection_config):
                        connection = create_connection(spec)
                        if hasattr(connection, "assign_nursery"):
                            connection.assign_nursery(nursery)

                        stack.enter_context(
                            app.connection_registry.use(
                                connection,
                                f"Crazyradio{index}",
                                description=f"Crazyradio connection {index}",
                                purpose=ConnectionPurpose.uavRadioLink,
                            )
                        )

                        task = partial(
                            CrazyradioScannerTask.create_and_run,
                            log=self.log,
                            channel=new_uav_tx_channel,
                        )

                        nursery.start_soon(
                            partial(app.supervise, connection, task=task)
                        )

                    # Wait for newly detected UAVs and spawn a task for each of them
                    async with new_uav_rx_channel:
                        async for address_space, index in new_uav_rx_channel:
                            uav = self._driver.get_or_create_uav(address_space, index)
                            nursery.start_soon(
                                CrazyflieHandlerTask(uav, debug=debug).run
                            )


class CrazyflieHandlerTask:
    """Class responsible for handling communication with a single Crazyflie
    drone.
    """

    def __init__(self, uav, debug: bool = False):
        """Constructor.

        Parameters:
            uav: the Crazyflie UAV to communicate with
            debug: whether to log the communication with the UAV on the console
        """
        self._uav = uav
        self._debug = bool(debug)

    async def run(self):
        """Implementation of the task itself."""
        async with self._uav.use(debug=self._debug) as cf:
            print("Validating log...")
            await cf.log.validate()
            print("Validated log.")
            await sleep_forever()


construct = CrazyflieDronesExtension
