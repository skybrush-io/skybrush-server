"""Application object for the Skybrush proxy server."""

from http.client import parse_headers
from io import BytesIO
from trio import MultiError, open_nursery, sleep_forever
from typing import Optional, Tuple

from flockwave.connections import (
    Connection,
    ConnectionSupervisor,
    create_connection,
    create_connection_factory,
)
from flockwave.server.configurator import AppConfigurator

from .logger import log

__all__ = ("app",)

PACKAGE_NAME = __name__.rpartition(".")[0]

CRLF = b"\r\n"
CRLFCRLF = b"\r\n\r\n"


def parse_content_length(headers: bytes) -> int:
    """Parses the value of the Content-Length header from the given raw HTTP
    headers.
    """
    parsed_headers = parse_headers(BytesIO(headers))
    if parsed_headers.get("transfer-encoding") == "chunked":
        raise RuntimeError("Chunked requests or responses are not supported")

    body_length = parsed_headers.get("content-length")
    return int(body_length) if body_length else 0


async def parse_http_headers_from_connection(
    conn: Connection
) -> Tuple[bytes, bytes, bytes]:
    """Parses the status line and the HTTP headers from the given connection.

    Returns:
        the status line, the raw headers and the initial fragment of the body
        that was also read, or `None` if the connection was closed prematurely
    """
    headers = []

    # Read headers first
    while True:
        data = await conn.read()
        if not data:
            return None

        headers.append(data)
        if CRLFCRLF in data:
            headers, _, body = b"".join(headers).partition(CRLFCRLF)
            status_line, _, headers = headers.partition(CRLF)
            return status_line, headers, body


class SkybrushProxyServer:
    """Main application object for the Skybrush proxy server.

    Attributes:
        config (dict): dictionary holding the configuration options of the
            application
        debug (bool): boolean flag to denote whether the application is in
            debugging mode
    """

    def __init__(self):
        self.config = {}
        self.debug = False

        self._nursery = None
        self._public_url_parts = None

        self._create_components()

    def _create_components(self):
        """Creates all the components and registries of the proxy.

        This function is called by the constructor once at construction time.
        You should not need to call it later.

        The configuration of the server is not loaded yet when this function is
        executed. Avoid querying the configuration of the server here because
        the settings will not be up-to-date yet. Use `prepare()` for any
        preparations that depend on the configuration.
        """
        # Creates an object whose responsibility is to restart connections
        # that closed unexpectedly
        self.connection_supervisor = ConnectionSupervisor()

    def prepare(self, config: Optional[str], debug: bool = False) -> Optional[int]:
        """Hook function that contains preparation steps that should be
        performed by the proxy before it starts establishing the connections
        to the remote and the local server.

        Parameters:
            config: name of the configuration file to load
            debug: whether to force the app into debug mode

        Returns:
            error code to terminate the app with if the preparation was not
            successful; ``None`` if the preparation was successful
        """
        configurator = AppConfigurator(
            self.config,
            environment_variable="SKYBRUSH_PROXY_SETTINGS",
            default_filename="skybrush-proxy.cfg",
            log=log,
            package_name=PACKAGE_NAME,
        )
        if not configurator.configure(config):
            return 1

        if debug or self.config.get("DEBUG"):
            self.debug = True

    async def run(self) -> None:
        # Helper function to ignore KeyboardInterrupt exceptions even if
        # they are wrapped in a Trio MultiError
        def ignore_keyboard_interrupt(exc):
            return None if isinstance(exc, KeyboardInterrupt) else exc

        self.local_connection_factory = create_connection_factory(
            self.config.get("LOCAL_SERVER")
        )
        remote_connection = create_connection(self.config.get("REMOTE_SERVER"))

        with MultiError.catch(ignore_keyboard_interrupt):
            async with open_nursery() as nursery:
                # nursery.start_soon(self.process_request_queue)
                nursery.start_soon(self.supervise_remote_connection, remote_connection)

    async def run_local_connection(self, conn: Connection) -> None:
        while True:
            await sleep_forever()

    async def run_remote_connection(self, conn: Connection) -> None:
        try:
            async with conn:
                while True:
                    should_close = await self.handle_single_request_from_remote_connection(
                        conn
                    )
                    if should_close:
                        break

        except Exception:
            log.exception("Unhandled exception")

    async def handle_single_request_from_remote_connection(
        self, conn: Connection
    ) -> None:
        parsed_stuff = await parse_http_headers_from_connection(conn)
        if parsed_stuff is None:
            return True

        # Parse the status line
        status_line, headers, body = parsed_stuff
        parts = status_line.split(b" ")
        if len(parts) < 3 or parts[2] != b"HTTP/1.0":
            raise RuntimeError("Only HTTP/1.0 requests are supported")

        log.info(parts[1].decode("ascii"), extra={"id": parts[0].decode("ascii")})

        # Parse the headers
        body_length = parse_content_length(headers)
        body_length -= len(body)

        # Read the body
        if body_length > 0:
            body = body + (await conn.read(body_length))

        # Forward the whole shebang to the local connection
        local_connection = self.local_connection_factory()
        async with local_connection:
            preamble = status_line + CRLF + headers + CRLFCRLF + body
            await local_connection.write(preamble)

            # Read the response so we know how many bytes to expect
            response_status_line, response_headers, response_body = await parse_http_headers_from_connection(
                local_connection
            )

            await conn.write(response_status_line + CRLF + response_headers + CRLFCRLF)
            await conn.write(response_body)

            response_length = parse_content_length(response_headers)
            bytes_read = len(response_body)

            while True:
                chunk = await local_connection.read()
                if chunk:
                    await conn.write(chunk)

                bytes_read += len(chunk)
                if not chunk or bytes_read >= response_length:
                    break

    async def supervise_local_connection(self, conn: Connection) -> None:
        await self.connection_supervisor.supervise(
            conn, task=self.run_local_connection, policy=None
        )

    async def supervise_remote_connection(self, conn: Connection) -> None:
        await self.connection_supervisor.supervise(
            conn, task=self.run_remote_connection, policy=None
        )


############################################################################

app = SkybrushProxyServer()
