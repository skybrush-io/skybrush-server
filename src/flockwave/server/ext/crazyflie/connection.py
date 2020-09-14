"""Connection object that manages a permanent connection to several Crazyflie
drones with a single Crazyradio.
"""

from trio import sleep_forever

from flockwave.connections.base import TaskConnectionBase

__all__ = ("CrazyradioConnection",)


class CrazyradioConnection(TaskConnectionBase):
    """Connection object that manages a permanent connection to several Crazyflie
    drones with a single Crazyradio.
    """

    def __init__(self, host: str, path: str = ""):
        """Constructor.

        Parameters:
            host: the integer index of the Crazyradio
            path: the channel number, the data rate and the ID prefix of the
                namespace where the Crazyflie drones are accessible, in the
                following format: ``/channel/data_rate/prefix``; e.g.:
                ``/80/2M/7E7E7E7E``.
        """
        from aiocflib.utils.addressing import RadioAddressSpace

        super().__init__()

        self._crazyradio_uri = f"radio://{host}"
        self._crazyflie_address_space = RadioAddressSpace.from_uri(
            f"radio://{host}{path}", length=64
        )
        self._radio = None

    async def _run(self, started):
        from aiocflib.drivers.crazyradio import Crazyradio

        radio = await Crazyradio.from_uri(self._crazyradio_uri)
        try:
            async with radio as self._radio:
                started()
                await sleep_forever()
        except Exception as ex:
            print(repr(ex))
            raise
        finally:
            self._radio = None

    @property
    def address_space(self):
        """Returns the address space associated to the connection.

        The address space is a sequence containing the addresses of all the
        potential Crazyflie drones that the connection can detect and handle.
        """
        return self._crazyflie_address_space

    async def scan(self, targets=None):
        """Scans the address space associated to the connection for Crazyflie
        drones or a part of it.

        Parameters:
            targets: the addresses to scan; `None` to scan the entire address
                space
        """
        return await self._radio.scan(targets)
