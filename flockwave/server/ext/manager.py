"""Extension manager class for Flockwave."""

from __future__ import absolute_import

import importlib

from .logger import log as base_log

__all__ = ("ExtensionManager", )

EXT_PACKAGE_NAME = __name__.rpartition(".")[0]
log = base_log.getChild("manager")


class ExtensionManager(object):
    """Central extension manager for a Flockwave server that manages
    the loading, configuration and unloading of extensions.
    """

    def __init__(self, app=None):
        """Constructor.

        Parameters:
            app (FlockwaveServer): the "application context" of the
                extension manager.
        """
        self._app = None
        self._extensions = {}
        self._num_clients = 0
        self.app = app

    @property
    def app(self):
        """The application context of the extension manager. This will also
        be passed on to the extensions when they are initialized.
        """
        return self._app

    @app.setter
    def app(self, value):
        if self._app is value:
            return

        if self._app is not None:
            self._app.num_clients_changed.disconnect(
                self._app_client_count_changed, sender=self._app
            )

        self._spindown_all_extensions()
        self._app = value
        self._num_clients = self._app.num_clients if self._app else 0
        if self._num_clients > 0:
            self._spinup_all_extensions()

        if self._app is not None:
            self._app.num_clients_changed.connect(
                self._app_client_count_changed, sender=self._app
            )

    def configure(self, configuration):
        """Conigures the extension manager.

        Extensions that were loaded earlier will be unloaded before loading
        the new ones with the given configuration.

        Parameters:
            configuration (dict): a dictionary mapping names of the
                extensions to their configuration.
        """
        for extension_name in self.loaded_extensions:
            self.unload(extension_name)
        for extension_name, extension_cfg in configuration.items():
            if not extension_name.startswith("_"):
                self.load(extension_name, extension_cfg)

    def _get_extension_by_name(self, extension_name):
        """Returns the extension object corresponding to the extension
        with the given name.

        Parameters:
            extension_name (str): the name of the extension

        Returns:
            object: the extension with the given name

        Raises:
            KeyError: if the extension with the given name is not loaded
        """
        return self._extensions[extension_name]

    def load(self, extension_name, configuration=None):
        """Loads an extension with the given name.

        The extension will be imported from the ``flockwave.server.ext``
        package. When the module contains a callable named ``construct()``,
        it will be called to construct a new instance of the extension.
        Otherwise, the entire module is assumed to be the extension
        instance.

        Extension instances should have methods named ``load()`` and
        ``unload()``; these methods will be called when the extension
        instance is loaded or unloaded. The ``load()`` method is always
        called with the application context, the configuration object of
        the extension and a logger instance that the extension should use
        for logging. The ``unload()`` method is always called without an
        argument.

        Parameters:
            extension_name (str): the name of the extension to load
            configuration (dict or None): the configuration dictionary for
                the extension. ``None`` is equivalent to an empty dict.
        """
        if extension_name in ("logger", "manager", "base", "__init__"):
            raise ValueError("invalid extension name: {0!r}"
                             .format(extension_name))

        log.info("Loading extension {0!r}".format(extension_name))

        module_name = "{0}.{1}".format(EXT_PACKAGE_NAME, extension_name)

        try:
            module = importlib.import_module(module_name)
        except ImportError:
            log.exception("Error while importing extension {0!r}"
                          .format(extension_name))
            return

        instance_factory = getattr(module, "construct", None)
        extension = instance_factory() if instance_factory else module

        func = getattr(extension, "load", None)
        if callable(func):
            try:
                extension_log = base_log.getChild(extension_name)
                func(self.app, configuration, extension_log)
            except Exception:
                log.exception("Error while loading extension {0!r}"
                              .format(extension_name))
                return

        self._extensions[extension_name] = extension
        if self._num_clients > 0:
            self._spinup_extension(extension)

    @property
    def loaded_extensions(self):
        """Returns a list containing the names of all the extensions that
        are currently loaded into the extension manager. The caller is free
        to modify the list; it will not affect the extension manager.

        Returns:
            list: the names of all the extensions that are currently loaded
        """
        return sorted(self._extensions.keys())

    def unload(self, extension_name):
        """Unloads the extension with the given name.

        Parameters:
            extension_name (str): the name of the extension to unload
        """
        try:
            extension = self._get_extension_by_name(extension_name)
        except KeyError:
            log.warning("Tried to unload extension {0!r} but it is "
                        "not loaded".format(extension_name))
            return

        if self._num_clients > 0:
            self._spindown_extension(extension)

        clean_unload = True
        func = getattr(extension, "unload", None)
        if callable(func):
            try:
                func()
            except Exception:
                clean_unload = False
                log.exception("Error while unloading extension {0!r}; "
                              "forcing unload".format(extension_name))

        del self._extensions[extension_name]

        message = "Unloaded extension {0!r}".format(extension_name)
        if clean_unload:
            log.info(message)
        else:
            log.warning(message)

    def _app_client_count_changed(self, sender):
        """Signal handler that is called whenever the number of clients
        connected to the app has changed.
        """
        old_value = self._num_clients
        self._num_clients = self.app.num_clients
        if self._num_clients == 0 and old_value != 0:
            self._spindown_all_extensions()
        elif self._num_clients != 0 and old_value == 0:
            self._spinup_all_extensions()

    def _spindown_all_extensions(self):
        """Iterates over all loaded extensions and spins down each one of
        them.
        """
        for extension_name in self._extensions:
            self._spindown_extension(extension_name)

    def _spinup_all_extensions(self):
        """Iterates over all loaded extensions and spins up each one of
        them.
        """
        for extension_name in self._extensions:
            self._spinup_extension(extension_name)

    def _spindown_extension(self, extension_name):
        """Spins down the given extension.

        This is done by calling the ``spindown()`` method or function of
        the extension, if any.

        Arguments:
            extension_name (str): the name of the extension to spin down.
        """
        extension = self._get_extension_by_name(extension_name)
        func = getattr(extension, "spindown", None)
        if callable(func):
            try:
                func()
            except Exception:
                log.exception("Error while spinning down extension {0!r}"
                              .format(extension_name))
                return

    def _spinup_extension(self, extension_name):
        """Spins up the given extension.

        This is done by calling the ``spinup()`` method or function of
        the extension, if any.

        Arguments:
            extension_name (str): the name of the extension to spin up.
        """
        extension = self._get_extension_by_name(extension_name)
        func = getattr(extension, "spinup", None)
        if callable(func):
            try:
                func()
            except Exception:
                log.exception("Error while spinning up extension {0!r}"
                              .format(extension_name))
                return
