"""Base class for extensions."""

from __future__ import annotations

from deprecated.sphinx import versionadded
from pathlib import Path
from typing import Generic, Optional, TYPE_CHECKING, TypeVar

from flockwave.ext.base import ExtensionBase

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer  # noqa
    from flockwave.server.model.uav import UAVDriver

__all__ = ("UAVExtension",)


class Extension(ExtensionBase["SkybrushServer"]):
    """Base class for extensions in the server application."""

    @versionadded(version="2.15.0")
    def get_cache_dir(self, name: Optional[str] = None) -> Path:
        """Returns the full path of a directory that the extension may use to
        store cached data.

        Args:
            name: name of the cache directory; will be the same as the name
                of the extension when omitted

        Returns:
            full path of a cache directory dedicated to the extension
        """
        assert self.app is not None
        return self.app.dirs.user_cache_path / "ext" / (name or self.name or "_unnamed")

    @versionadded(version="2.15.0")
    def get_data_dir(self, name: Optional[str] = None) -> Path:
        """Returns the full path of a directory that the extension may use to
        store persistent data.

        Args:
            name: name of the data directory; will be the same as the name
                of the extension when omitted

        Returns:
            full path of a data directory dedicated to the extension
        """
        assert self.app is not None
        return self.app.dirs.user_data_path / "ext" / (name or self.name or "_unnamed")


D = TypeVar("D", bound="UAVDriver")


class UAVExtension(Extension, Generic[D]):
    """Base class for extensions that intend to provide support for a
    specific type of UAVs.

    Subclasses should override the ``_create_driver()`` method to create
    the actual driver instance that the extension will use, and the
    ``configure_driver()`` method to configure the driver.
    """

    _driver: Optional[D] = None

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

        if self.driver is not None:
            self.configure_driver(self.driver, configuration)

    def configure_driver(self, driver: D, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver: the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        pass

    def _create_driver(self) -> Optional[D]:
        """Creates the driver object that the extension will use. It is
        not required to associate the driver to the current application;
        the extension will do it.

        Returns:
            the driver that the extension will use, or ``None`` if the extension
            does not need a driver
        """
        return None

    def _update_driver_from_app(self) -> None:
        """Updates the driver object in the extension when the associated
        app has changed.
        """
        if self._driver:
            old_app: Optional["SkybrushServer"] = self._driver.app
            if old_app:
                old_app.uav_driver_registry.remove(self._driver)

        app = self.app
        self._driver = self._create_driver() if app else None

        if self._driver is not None:
            assert app is not None
            self._driver.app = app
            app.uav_driver_registry.add(self._driver)

    @property
    def driver(self):
        """The driver that is responsible for handling communication with
        the UAVs that the extension provides.
        """
        return self._driver

    def on_app_changed(self, old_app, new_app):
        self._update_driver_from_app()
