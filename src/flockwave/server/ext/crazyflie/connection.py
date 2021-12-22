"""Connection object that manages a permanent connection to several Crazyflie
drones with a single Crazyradio.
"""

from trio import Event
from typing import AsyncContextManager, Callable, ClassVar, Optional

from aiocflib.crtp.broadcaster import Broadcaster
from aiocflib.crtp.crtpstack import CRTPPort
from aiocflib.utils.addressing import parse_radio_uri

from flockwave.connections.base import TaskConnectionBase

__all__ = ("CrazyradioConnection", "parse_radio_uri")


class CrazyradioConnection(TaskConnectionBase):
    """Connection object that manages a permanent connection to several Crazyflie
    drones with a single Crazyradio.
    """

    SCHEME: ClassVar[str] = "crazyradio"
    """The connection scheme under which this connection should be registered
    in the server.
    """

    _radio = None
    _radio_factory: Optional[Callable[[], AsyncContextManager]] = None
    _request_close_event: Optional[Event] = None

    @classmethod
    def parse_radio_index_from_uri(cls, uri: str) -> Optional[int]:
        """Parses the given connection URI and returns the index of the
        Crazyradio that it refers to, or ``None`` if the connection URI is not
        a Crazyflie connection URI or it does not use a radio.
        """
        if not uri.startswith(cls.SCHEME):
            return None

        try:
            parsed = parse_radio_uri(uri, allow_prefix=True)
        except Exception:
            # Probably not a radio URI
            return None

        if "index" in parsed and isinstance(parsed["index"], int):
            return parsed["index"]
        else:
            return None

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
        self._broadcaster = None
        self._radio = None
        self._request_close_event = None

    async def _run(self, started):
        from aiocflib.crtp.drivers.radio import SharedCrazyradio

        uri_prefix = self._crazyflie_address_space.uri_prefix

        try:
            async with SharedCrazyradio(self._crazyradio_index) as self._radio:
                async with Broadcaster(uri_prefix) as self._broadcaster:
                    self._request_close_event = Event()
                    started()
                    await self._request_close_event.wait()
        finally:
            self._broadcaster = None
            self._request_close_event = None
            self._radio = None

    @property
    def address_space(self):
        """Returns the address space associated to the connection.

        The address space is a sequence containing the addresses of all the
        potential Crazyflie drones that the connection can detect and handle.
        """
        return self._crazyflie_address_space

    async def broadcast(self, port: CRTPPort, data: bytes) -> None:
        """Broadcasts a CRTP packet to all Crazyflie drones in the range of the
        connection.

        No-op if the radio is not connected yet or is not connected any more.

        Parameters:
            packet: the packet to broadcast
        """
        if self._broadcaster:
            await self._broadcaster.send_packet(port=port, data=data)

    def notify_error(self) -> None:
        """Notifies the connection that an error happened while using the radio
        and the connection should be closed.
        """
        if self._request_close_event:
            self._request_close_event.set()

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
