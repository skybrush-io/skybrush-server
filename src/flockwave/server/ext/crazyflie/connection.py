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

    def __init__(self, host: str, path: str = "", length: int = 64):
        """Constructor.

        Parameters:
            host: the integer index of the Crazyradio
            path: the channel number, the data rate and the ID prefix of the
                namespace where the Crazyflie drones are accessible, in the
                following format: ``/channel/data_rate/prefix``; e.g.:
                ``/80/2M/7E7E7E7E``.
            length: the length of the address space of the Crazyflies
        """
        from aiocflib.utils.addressing import RadioAddressSpace

        super().__init__()

        try:
            self._crazyradio_index = int(host)
        except ValueError:
            raise RuntimeError("Radio index must be integer")

        self._crazyflie_address_space = RadioAddressSpace.from_uri(
            f"bradio://{host}{path}", length=length
        )
        self._radio = None

    async def _run(self, started):
        from aiocflib.crtp.drivers.radio import SharedCrazyradio

        try:
            async with SharedCrazyradio(self._crazyradio_index) as self._radio:
                started()
                await sleep_forever()
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
        if self._radio:
            return await self._radio.scan(targets or self.address_space)
        else:
            return []
