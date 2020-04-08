"""Application object for the Skybrush gateway server."""

import logging

from copy import deepcopy
from trio import current_time, MultiError, Nursery, open_nursery, sleep
from typing import Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from hypercorn.config import Config as HyperConfig
from hypercorn.trio import serve
from jwt import decode

from flockwave.networking import format_socket_address
from flockwave.server.configurator import AppConfigurator

from .asgi_app import update_api, app as asgi_app
from .logger import log
from .workers import WorkerManager

__all__ = ("app",)

PACKAGE_NAME = __name__.rpartition(".")[0]


class SkybrushGatewayServer:
    """Main application object for the Skybrush gateway server.

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
        """Creates all the components and registries of the server.

        This function is called by the constructor once at construction time.
        You should not need to call it later.

        The configuration of the server is not loaded yet when this function is
        executed. Avoid querying the configuration of the server here because
        the settings will not be up-to-date yet. Use `prepare()` for any
        preparations that depend on the configuration.
        """
        self.worker_manager = WorkerManager()

    def get_public_url_of_worker_from_port(self, port: int) -> str:
        """Returns the public URL where a worker is accessible, given the port
        that the worker listens on.

        Defaults to the same host where the server listens on. The configuration
        may specify an alternative URL template to use.
        """
        if self._public_url_parts:
            host, _, _ = self._public_url_parts.netloc.partition(":")
            parts = self._public_url_parts._replace(netloc=f"{host}:{port}")
            return urlunparse(parts)
        else:
            host, _ = self._get_listening_address()
            scheme = "https" if self._is_listening_securely() else "http"
            return f"{scheme}://{host}:{port}"

    def prepare(self, config: Optional[str]) -> Optional[int]:
        """Hook function that contains preparation steps that should be
        performed by the server before it starts serving requests.

        Parameters:
            config: name of the configuration file to load

        Returns:
            error code to terminate the app with if the preparation was not
            successful; ``None`` if the preparation was successful
        """
        configurator = AppConfigurator(
            self.config,
            environment_variable="SKYBRUSH_GATEWAY_SETTINGS",
            default_filename="skybrush-gateway.cfg",
            log=log,
            package_name=PACKAGE_NAME,
        )
        if not configurator.configure(config):
            return 1

        self._public_url_parts = (
            urlparse(self.config["PUBLIC_URL"])
            if self.config.get("PUBLIC_URL")
            else None
        )

        self.worker_manager.max_count = self.config.get("MAX_WORKERS", 1)
        self.worker_manager.worker_config_factory = self._create_worker_config

    async def run(self) -> None:
        # Helper function to ignore KeyboardInterrupt exceptions even if
        # they are wrapped in a Trio MultiError
        def ignore_keyboard_interrupt(exc):
            return None if isinstance(exc, KeyboardInterrupt) else exc

        with MultiError.catch(ignore_keyboard_interrupt):
            async with open_nursery() as nursery:
                await self._serve(nursery)

    def validate_jwt_token(self, token: bytes):
        secret = self.config.get("JWT_SECRET")
        if not secret:
            raise ValueError("no JWT secret was configured")
        else:
            return decode(token, secret, algorithms=["HS256"])

    def _create_worker_config(self, index: int) -> Tuple[Any, int]:
        config = deepcopy(self.config.get("WORKER_CONFIG", {}))
        base_port = self.config.get("PORT")
        port = base_port + index + 1
        if "EXTENSIONS" in config:
            if "http_server" in config["EXTENSIONS"]:
                config["EXTENSIONS"]["http_server"]["port"] = port
        return config, port

    def _get_listening_address(self) -> Tuple[str, int]:
        """Returns the hostname and port where the server is listening, or
        `None` if the address is not configured in the configuration file.
        """
        host, port = self.config.get("HOST"), self.config.get("PORT")
        if (not host and host != "") or not port:
            return None

        port = int(port)
        return host, port

    def _is_listening_securely(self) -> bool:
        """Returns whether the application is listening on a secure socket."""
        return self.config.get("certfile") and self.config.get("keyfile")

    async def _serve(self, nursery: Nursery) -> None:
        address = self._get_listening_address()
        if address is None:
            log.warn("HTTP server address is not specified in configuration")
            return

        host, port = address

        # Don't show info messages by default (unless the app is in debug mode),
        # show warnings and errors only
        server_log = log.getChild("hypercorn")
        if not self.debug:
            server_log.setLevel(logging.WARNING)

        # Create configuration for Hypercorn
        config = HyperConfig()
        config.accesslog = server_log
        config.bind = [f"{host}:{port}"]
        # config.certfile = self.config.get("certfile")
        config.errorlog = server_log
        # config.keyfile = self.config.get("keyfile")
        config.use_reloader = False

        secure = bool(config.ssl_enabled)

        retries = 0
        max_retries = 3

        update_api(self)

        while True:
            log.info(
                "Starting {1} server on {0}...".format(
                    format_socket_address(address), "HTTPS" if secure else "HTTP"
                )
            )

            started_at = current_time()

            nursery.start_soon(self.worker_manager.run)

            try:
                await serve(asgi_app, config)
            except Exception:
                # Server crashed -- maybe a change in IP address? Let's try
                # again if we have not reached the maximum retry count.
                if current_time() - started_at >= 5:
                    retries = 0

                if retries < max_retries:
                    log.error("Server stopped unexpectedly, retrying...")
                    await sleep(1)
                    retries += 1
                else:
                    # Re-raise the exception
                    raise
            else:
                break
            finally:
                nursery.cancel_scope.cancel()


############################################################################

app = SkybrushGatewayServer()
