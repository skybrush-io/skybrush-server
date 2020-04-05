"""Application object for the Skybrush gateway server."""

import logging

from trio import current_time, MultiError, Nursery, open_nursery, sleep
from typing import Optional

from hypercorn.config import Config as HyperConfig
from hypercorn.trio import serve

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

        self.worker_manager.max_count = self.config.get("MAX_WORKERS", 1)

    async def run(self) -> None:
        # Helper function to ignore KeyboardInterrupt exceptions even if
        # they are wrapped in a Trio MultiError
        def ignore_keyboard_interrupt(exc):
            return None if isinstance(exc, KeyboardInterrupt) else exc

        with MultiError.catch(ignore_keyboard_interrupt):
            async with open_nursery() as nursery:
                await self._serve(nursery)

    async def _serve(self, nursery: Nursery) -> None:
        address = host, port = self.config.get("HOST"), self.config.get("PORT")
        if not host or not port:
            log.warn("HTTP server address is not specified in configuration")
            return

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

            nursery.start_soon(self.worker_manager.run, nursery)

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
