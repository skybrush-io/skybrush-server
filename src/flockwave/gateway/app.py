"""Application object for the Skybrush gateway server."""

from trio import MultiError, sleep_forever
from typing import Optional

from flockwave.server.configurator import AppConfigurator

from .logger import log

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
        pass

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

    async def run(self) -> None:
        # Helper function to ignore KeyboardInterrupt exceptions even if
        # they are wrapped in a Trio MultiError
        def ignore_keyboard_interrupt(exc):
            return None if isinstance(exc, KeyboardInterrupt) else exc

        with MultiError.catch(ignore_keyboard_interrupt):
            await sleep_forever()


############################################################################

app = SkybrushGatewayServer()
