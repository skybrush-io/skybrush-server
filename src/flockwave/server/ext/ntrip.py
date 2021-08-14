"""Extension that teaches the Skybrush server how to construct NTRIP connections
yielding RTCMv2 and RTCMv3 messages from a remote NTRIP server.
"""

from typing import Optional

from flockwave.connections import ConnectionBase, create_connection, ReadableConnection
from flockwave.gps.http.response import Response
from flockwave.gps.ntrip.client import NtripClient

__all__ = ("load", "unload")


class NTRIPConnection(ConnectionBase, ReadableConnection[bytes]):
    """Connection to a remote NTRIP server."""

    _stream: Optional[Response]

    def __init__(
        self,
        host: str,
        mountpoint: Optional[str] = None,
        port: int = 2101,
        username: Optional[str] = None,
        password: Optional[str] = None,
        version: Optional[int] = None,
        **kwds
    ):
        """Constructor.

        Parameters:
            host: the hostname of the server to connect to
            port: the port to connect to; defaults to the standard NTRIP port
            mountpoint: the mountpoint to read the RTCM packets from
            username: the username to use when authenticating with the server
            password: the password to use when authenticating with the server
            version: the NTRIP protocol version that the server speaks;
                `None` means the latest available version

        Keyword arguments:
            path: an alias to "mountpoint"; the leading slash will be stripped
        """
        path = kwds.pop("path", None)

        super().__init__(**kwds)

        if not mountpoint and path:
            mountpoint = path.lstrip("/")

        if not mountpoint:
            raise ValueError("mountpoint must not be empty")

        self._stream = None
        self._client_params = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "mountpoint": mountpoint,
            "version": version,
        }

    async def _open(self) -> None:
        if self._stream is not None:
            return

        client = NtripClient.create(**self._client_params)
        self._stream = await client.get_stream()

    async def _close(self) -> None:
        if self._stream is None:
            return

        try:
            await self._stream.aclose()
        finally:
            self._stream = None

    async def read(self, max_bytes: Optional[int] = None) -> bytes:
        assert self._stream is not None
        return await self._stream.read(max_bytes)


def load():
    create_connection.register("ntrip", NTRIPConnection)


def unload():
    create_connection.unregister("ntrip")


description = "Connections to NTRIP servers and casters"
schema = {}
