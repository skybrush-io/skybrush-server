"""Base class for extensions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flockwave.ext.base import ExtensionBase

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer  # noqa

__all__ = ("UAVExtension",)


class Extension(ExtensionBase["SkybrushServer"]):
    """Base class for extensions in the server application."""

    pass


class UAVExtension(Extension):
    """Base class for extensions that intend to provide support for a
    specific type of UAVs.

    Subclasses should override the ``_create_driver()`` method to create
    the actual driver instance that the extension will use, and the
    ``configure_driver()`` method to configure the driver.
    """

    def __init__(self):
        super().__init__()
        self._driver = None

    def create_device_tree_mutation_context(self):
        """Returns a context that can be used in a ``with`` statement to
        encapsulate a block of code that may modify the channels in the
        device tree of some UAVs.

        Modifications to the channel nodes in a device tree should always be
        done in a mutation context to ensure that clients are notified about
        the modifications.
        """
        assert self.app is not None
        return self.app.device_tree.create_mutator()

    def configure(self, configuration):
        super().configure(configuration)
        self.configure_driver(self.driver, configuration)

    def configure_driver(self, driver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver (UAVDriver): the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        pass

    def _create_driver(self):
        """Creates the driver object that the extension will use. It is
        not required to associate the driver to the current application;
        the extension will do it.

        Returns:
            Optional[UAVDriver]: the driver that the extension will use,
                or ``None`` if the extension does not need a driver
        """
        return None

    def _update_driver_from_app(self) -> None:
        """Updates the driver object in the extension when the associated
        app has changed.
        """
        app = self.app
        if app is None:
            self._driver = None
        else:
            self._driver = self._create_driver()
            if self._driver is not None:
                self._driver.app = app

    @property
    def driver(self):
        """The driver that is responsible for handling communication with
        the UAVs that the extension provides.
        """
        return self._driver

    def on_app_changed(self, old_app, new_app):
        self._update_driver_from_app()
