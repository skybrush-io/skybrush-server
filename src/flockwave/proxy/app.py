"""Application object for the Skybrush proxy server."""

from http.client import parse_headers
from io import BytesIO
from trio import MultiError, open_nursery, sleep_forever
from typing import Tuple

from flockwave.app_framework import DaemonApp
from flockwave.app_framework.configurator import AppConfigurator
from flockwave.connections import (
    Connection,
    StreamConnection,
    create_connection,
    create_connection_factory,
)
from flockwave.networking import format_socket_address
from flockwave.server.utils.packaging import is_packaged

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
    conn: Connection,
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


async def copy_stream(source: StreamConnection, target: StreamConnection) -> None:
    while True:
        data = await source.read()
        if not data:
            break

        await target.write(data)


class SkybrushProxyServer(DaemonApp):
    """Main application object for the Skybrush proxy server."""

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
        address = getattr(conn, "address", None)
        assert address is not None

        try:
            log.info(f"Opened connection to {format_socket_address(address)}")
            async with conn:
                while True:
                    should_close = (
                        await self.handle_single_request_from_remote_connection(conn)
                    )
                    if should_close:
                        break

        except Exception:
            log.exception("Unhandled exception")
        finally:
            log.info(f"Closed connection to {format_socket_address(address)}")

    async def run_remote_connection_new(self, conn: Connection) -> None:
        # TODO(ntamas): this should be conceptually simpler and not dependent
        # on HTTP, but it does not work yet for some reason. Investigate.
        address = getattr(conn, "address", None)
        assert address is not None

        try:
            log.info(f"Opened connection to {format_socket_address(address)}")
            async with conn:
                local_conn = self.local_connection_factory()
                async with local_conn:
                    async with open_nursery() as nursery:
                        nursery.start_soon(copy_stream, conn, local_conn)
                        nursery.start_soon(copy_stream, local_conn, conn)
        except Exception:
            log.exception("Unhandled exception")
        finally:
            log.info(f"Closed connection to {format_socket_address(address)}")

    async def handle_single_request_from_remote_connection(
        self, conn: Connection
    ) -> bool:
        """Handles a single request from a remote connection. Returns whether
        the connection should be closed after processing the request.
        """
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
            (
                response_status_line,
                response_headers,
                response_body,
            ) = await parse_http_headers_from_connection(local_connection)

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

        return False

    async def supervise_local_connection(self, conn: Connection) -> None:
        assert self.connection_supervisor is not None
        await self.connection_supervisor.supervise(
            conn, task=self.run_local_connection, policy=None
        )

    async def supervise_remote_connection(self, conn: Connection) -> None:
        assert self.connection_supervisor is not None
        await self.connection_supervisor.supervise(
            conn, task=self.run_remote_connection, policy=None
        )

    def _setup_app_configurator(self, configurator: AppConfigurator) -> None:
        configurator.safe = is_packaged()


############################################################################

app = SkybrushProxyServer("skybrush-proxy", PACKAGE_NAME)
