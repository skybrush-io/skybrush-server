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
            app (object): the "application context" of the extension
                manager. This is an opaque object that will be passed on
                to the extensions when they are initialized.
        """
        self.app = app
        self._extensions = {}

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

        if callable(getattr(extension, "load", None)):
            try:
                extension_log = base_log.getChild(extension_name)
                extension.load(self.app, configuration, extension_log)
            except ImportError:
                log.exception("Error while loading extension {0!r}"
                              .format(extension_name))
                return

        self._extensions[extension_name] = extension

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

        clean_unload = True
        if callable(getattr(extension, "unload", None)):
            try:
                extension.unload()
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
