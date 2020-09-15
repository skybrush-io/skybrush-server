"""Driver class for Crazyflie drones."""

from collections import defaultdict
from contextlib import asynccontextmanager

from flockwave.server.ext.logger import log
from flockwave.server.model.uav import UAVBase, UAVDriver
from flockwave.spec.ids import make_valid_object_id

__all__ = ("CrazyflieDriver",)


class CrazyflieDriver(UAVDriver):
    """Driver class for Crazyflie drones.

    Attributes:
        app (SkybrushServer): the app in which the driver lives
        id_format (str): Python format string that receives a numeric
            drone ID in the flock and returns its preferred formatted
            identifier that is used when the drone is registered in the
            server, or any other object that has a ``format()`` method
            accepting a single integer as an argument and returning the
            preferred UAV identifier
    """

    def __init__(self, app=None, id_format="{0:02}"):
        """Constructor.

        Parameters:
            app (SkybrushServer): the app in which the driver lives
            id_format (str): the format of the UAV IDs used by this driver.
                See the class documentation for more details.
        """
        super().__init__()

        self.app = app
        self.id_format = id_format
        self.log = log.getChild("crazyflie").getChild("driver")

        self._uav_ids_by_address_space = defaultdict(dict)

    def _create_uav(self, formatted_id: str) -> "CrazyflieUAV":
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            formatted_id: the formatted string identifier of the UAV
                to create

        Returns:
            an appropriate UAV object
        """
        return CrazyflieUAV(formatted_id, driver=self)

    def get_or_create_uav(self, address_space, index: int) -> "CrazyflieUAV":
        """Retrieves the UAV with the given index in the given address space
        or creates one if the driver has not seen a UAV with the given index in
        the given address space yet.

        Parameters:
            address_space: the address space
            index: the index of the address within the address space

        Returns:
            an appropriate UAV object
        """
        uav_id_map = self._uav_ids_by_address_space.get(address_space)
        formatted_id = uav_id_map.get(index) if uav_id_map else None
        if formatted_id is None:
            formatted_id = make_valid_object_id(
                self.id_format.format(index, address_space)
            )
            self._uav_ids_by_address_space[address_space][index] = formatted_id

        uav = self.app.object_registry.add_if_missing(
            formatted_id, factory=self._create_uav
        )
        if uav.uri is None:
            uav.uri = address_space[index]

        return uav


class CrazyflieUAV(UAVBase):
    """Subclass for UAVs created by the driver for Crazyflie drones.

    Attributes:
        uri: the Crazyflie URI of the drone
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.uri = None

    @asynccontextmanager
    async def use(self, debug: bool = False):
        """Async context manager that establishes a low-level connection to the
        drone given its URI when the context is entered, and closes the
        connection when the context is exited.

        Parameters:
            debug: whether to print the messages passed between the drone and
                the server to the console
        """
        from aiocflib.crazyflie import Crazyflie

        uri = self.uri

        if debug and "+log" not in uri:
            uri = uri.replace("://", "+log://")

        async with Crazyflie(uri) as drone:
            yield drone
