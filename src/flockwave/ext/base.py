"""Base class for extensions."""

from logging import Logger
from typing import Any, Dict

__all__ = ("ExtensionBase",)


Configuration = Dict[str, Any]


class ExtensionBase:
    """Interface specification for Flockwave extensions."""

    def __init__(self):
        """Constructor."""
        self._app = None
        self.log = None

    @property
    def app(self):
        """The application that the extension is attached to."""
        return self._app

    @app.setter
    def app(self, value):
        old_value = self._app
        self._app = value
        self.on_app_changed(old_value, self._app)

    def configure(self, configuration: Configuration) -> None:
        """Configures the extension with the given configuration object.

        This method is called only once from :meth:`load()`_ during the
        initialization of the extension.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def load(self, app, configuration: Configuration, logger: Logger) -> None:
        """Handler that is called by the extension manager when the
        extension is loaded into the application.

        Typically, you don't need to override this method; override
        :meth:`configure()` instead.

        Arguments:
            app: the application
            configuration: the extension-specific configuration dictionary of
                the application
            logger: a logger object that the extension may use to write to the
                application log
        """
        self.app = app
        self.log = logger
        self.configure(configuration)

    def on_app_changed(self, old_app, new_app) -> None:
        """Handler that is called when the extension is associated to an
        application.

        Arguments:
            old_app: the old application
            new_app: the new application
        """
        pass

    def spindown(self) -> None:
        """Handler that is called by the extension manager when the
        last client disconnects from the server.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def spinup(self) -> None:
        """Handler that is called by the extension manager when the
        first client connects to the server.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def teardown(self) -> None:
        """Tears down the extension and prepares it for unloading.

        This method is called only once from `unload()`_ during the
        unloading of the extension.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def unload(self, app) -> None:
        """Handler that is called by the extension manager when the
        extension is unloaded.

        Typically, you don't need to override this method; override
        `teardown()` instead.

        Arguments:
            app: the application; provided for sake of API compatibility with
                simple classless extensions where the module provides a single
                `unload()` function
        """
        self.teardown()
        self.log = None
        self.app = None
