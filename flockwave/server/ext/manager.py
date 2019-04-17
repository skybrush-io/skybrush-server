"""Extension manager class for Flockwave."""

from __future__ import absolute_import

import importlib

from blinker import Signal
from future.utils import iteritems
from pkgutil import get_loader

from .logger import log as base_log

from ..utils import keydefaultdict

__all__ = ("ExtensionManager", )

EXT_PACKAGE_NAME = __name__.rpartition(".")[0]
log = base_log.getChild("manager")


class ExtensionData(object):
    """Data that the extension manager stores related to each extension.

    Attributes:
        name (str): the name of the extension
        api_proxy (object): the API object that the extension exports
        configuration (object): the configuration object of the extension
        instance (object): the loaded instance of the extension
        dependents (Set[str]): names of other loaded extensions that depend
            on this extension
        loaded (bool): whether the extension is loaded
    """

    def __init__(self, name, configuration=None):
        """Constructor."""
        self.api_proxy = None
        self.name = name
        self.configuration = configuration if configuration is not None else {}
        self.dependents = set()
        self.instance = None
        self.loaded = False


class ExtensionManager(object):
    """Central extension manager for a Flockwave server that manages
    the loading, configuration and unloading of extensions.


    Attributes:
        loaded (Signal): signal that is sent by the extension manager when
            an extension has been configured and loaded. The signal has two
            keyword arguments: ``name`` and ``extension``.

        unloaded (Signal): signal that is sent by the extension manager when
            an extension has been unloaded. The signal has two keyword
            arguments: ``name`` and ``extension``.
    """

    loaded = Signal()
    unloaded = Signal()

    def __init__(self, app=None):
        """Constructor.

        Parameters:
            app (FlockwaveServer): the "application context" of the
                extension manager.
        """
        self._app = None
        self._extensions = keydefaultdict(self._create_extension_data)
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
        loaded_extensions = set(self.loaded_extensions)

        self.teardown()

        for extension_name, extension_cfg in iteritems(configuration):
            ext = self._extensions[extension_name]
            ext.configuration = dict(extension_cfg)
            loaded_extensions.add(extension_name)

        for extension_name in sorted(loaded_extensions):
            ext = self._extensions[extension_name]
            enabled = ext.configuration.get("enabled", True)
            if enabled:
                self.load(extension_name)

    def _create_extension_data(self, extension_name):
        """Creates a helper object holding all data related to the extension
        with the given name.

        Parameters:
            extension_name (str): the name of the extension

        Raises:
            KeyError: if the extension with the given name does not exist
        """
        if not self.exists(extension_name):
            raise KeyError(extension_name)
        else:
            data = ExtensionData(extension_name)
            data.api_proxy = ExtensionAPIProxy(self, extension_name)
            return data

    def _get_loaded_extension_by_name(self, extension_name):
        """Returns the extension object corresponding to the extension
        with the given name if it is loaded.

        Parameters:
            extension_name (str): the name of the extension

        Returns:
            object: the extension with the given name

        Raises:
            KeyError: if the extension with the given name is not declared in
                the configuration file or if it is not loaded
        """
        if extension_name not in self._extensions:
            raise KeyError(extension_name)

        ext = self._extensions[extension_name]
        if not ext.loaded or ext.instance is None:
            raise KeyError(extension_name)
        else:
            return ext.instance

    def _get_module_for_extension(self, extension_name):
        """Returns the module that contains the given extension.

        Parameters:
            extension_name (str): the name of the extension

        Returns:
            module: the module containing the extension with the given name
        """
        module_name = self._get_module_name_for_extension(extension_name)
        return importlib.import_module(module_name)

    def _get_module_name_for_extension(self, extension_name):
        """Returns the name of the module that should contain the given
        extension.

        Returns:
            str: the full, dotted name of the module that should contain the
                extension with the given name
        """
        return "{0}.{1}".format(EXT_PACKAGE_NAME, extension_name)

    def exists(self, extension_name):
        """Returns whether the extension with the given name exists,
        irrespectively of whether it was loaded already or not.

        Parameters:
            extension_name (str): the name of the extension

        Returns:
            bool: whether the extension exists
        """
        module_name = self._get_module_name_for_extension(extension_name)
        return get_loader(module_name) is not None

    def import_api(self, extension_name):
        """Imports the API exposed by an extension.

        Extensions *may* have a dictionary named ``exports`` that allows the
        extension to export some of its variables, functions or methods.
        Other extensions may access the exported members of an extension by
        calling the `import_api`_ method of the extension manager.

        This function supports "lazy imports", i.e. one may import the API
        of an extension before loading the extension. When the extension
        is not loaded, the returned API object will have a single property
        named ``loaded`` that is set to ``False``. When the extension is
        loaded, the returned API object will set ``loaded`` to ``True``.
        Attribute retrievals on the returned API object are forwarded to the
        API of the extension.

        Parameters:
            extension_name (str): the name of the extension whose API is to
                be imported

        Returns:
            ExtensionAPIProxy: a proxy object to the API of the extension
                that forwards attribute retrievals to the API, except for
                the property named ``loaded``, which returns whether the
                extension is loaded or not.

        Raises:
            KeyError: if the extension with the given name does not exist
        """
        return self._extensions[extension_name].api_proxy

    def load(self, extension_name):
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

        Extensions may declare dependencies if they provide a function named
        ``get_dependencies()``. The function must return a list of extension
        names that must be loaded _before_ the extension itself is loaded.
        This function will take care of loading all dependencies before
        loading the extension itself.

        Parameters:
            extension_name (str): the name of the extension to load
        """
        return self._load(extension_name, forbidden=[])

    @property
    def loaded_extensions(self):
        """Returns a list containing the names of all the extensions that
        are currently loaded into the extension manager. The caller is free
        to modify the list; it will not affect the extension manager.

        Returns:
            list: the names of all the extensions that are currently loaded
        """
        return sorted(key for key, ext in iteritems(self._extensions)
                      if ext.loaded)

    def is_loaded(self, extension_name):
        """Returns whether the given extension is loaded."""
        try:
            self._get_loaded_extension_by_name(extension_name)
            return True
        except KeyError:
            return False

    def teardown(self):
        """Tears down the extension manager and prepares it for destruction."""
        # TODO(ntamas): the unloading order should not be alphabetical but in
        # the order in which the extensions were loaded
        for ext_name in reversed(self.loaded_extensions):
            self.unload(ext_name)

    def unload(self, extension_name):
        """Unloads the extension with the given name.

        Parameters:
            extension_name (str): the name of the extension to unload
        """
        # TODO(ntamas): prevent the unloading of the extension if there is
        # at least one other loaded extension that depends on this one
        try:
            extension = self._get_loaded_extension_by_name(extension_name)
        except KeyError:
            log.warning("Tried to unload extension {0!r} but it is "
                        "not loaded".format(extension_name))
            return

        if self._num_clients > 0:
            self._spindown_extension(extension_name)

        clean_unload = True
        func = getattr(extension, "unload", None)
        if callable(func):
            try:
                func(self.app)
            except Exception:
                clean_unload = False
                log.exception("Error while unloading extension {0!r}; "
                              "forcing unload".format(extension_name))

        self._extensions[extension_name].loaded = False
        self._extensions[extension_name].instance = None

        for dependency in self._get_dependencies_of_extension(extension_name):
            self._extensions[dependency].dependents.remove(extension_name)

        self.unloaded.send(self, name=extension_name, extension=extension)

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

    def _get_dependencies_of_extension(self, extension_name):
        """Determines the list of extensions that a given extension depends
        on directly.

        Parameters:
            extension_name (str): the name of the extension

        Returns:
            Set[str]: the names of the extensions that the given extension
                depends on
        """
        try:
            module = self._get_module_for_extension(extension_name)
        except ImportError:
            log.exception("Error while importing extension {0!r}"
                          .format(extension_name))
            raise

        func = getattr(module, "get_dependencies", None)
        if callable(func):
            try:
                dependencies = func()
            except Exception:
                log.exception("Error while determining dependencies of "
                              "extension {0!r}".format(extension_name))
                dependencies = None
        else:
            dependencies = getattr(module, "dependencies", None)

        return set(dependencies or [])

    def _load(self, extension_name, forbidden):
        if extension_name in forbidden:
            cycle = forbidden + [extension_name]
            log.error("Dependency cycle detected: {0}".format(
                " -> ".join(map(str, cycle))
            ))
            return

        self._ensure_dependencies_loaded(extension_name, forbidden)
        if not self.is_loaded(extension_name):
            return self._load_single_extension(extension_name)

    def _load_single_extension(self, extension_name):
        """Loads an extension with the given name, assuming that all its
        dependencies are already loaded.

        This function is internal; use `load()` instead if you want to load
        an extension programmatically, and it will take care of loading all
        the dependencies as well.

        Parameters:
            extension_name (str): the name of the extension to load
        """
        if extension_name in ("logger", "manager", "base", "__init__"):
            raise ValueError("invalid extension name: {0!r}"
                             .format(extension_name))

        configuration = self._extensions[extension_name].configuration

        log.info("Loading extension {0!r}".format(extension_name))
        try:
            module = self._get_module_for_extension(extension_name)
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
                result = func(self.app, configuration, extension_log)
            except Exception:
                log.exception("Error while loading extension {0!r}"
                              .format(extension_name))
                return None

        self._extensions[extension_name].instance = extension
        self._extensions[extension_name].loaded = True

        for dependency in self._get_dependencies_of_extension(extension_name):
            self._extensions[dependency].dependents.add(extension_name)

        self.loaded.send(self, name=extension_name, extension=extension)

        if self._num_clients > 0:
            self._spinup_extension(extension)

        return result

    def _spindown_all_extensions(self):
        """Iterates over all loaded extensions and spins down each one of
        them.
        """
        for extension_name in self.loaded_extensions:
            self._spindown_extension(extension_name)

    def _spinup_all_extensions(self):
        """Iterates over all loaded extensions and spins up each one of
        them.
        """
        for extension_name in self.loaded_extensions:
            self._spinup_extension(extension_name)

    def _spindown_extension(self, extension_name):
        """Spins down the given extension.

        This is done by calling the ``spindown()`` method or function of
        the extension, if any.

        Arguments:
            extension_name (str): the name of the extension to spin down.
        """
        extension = self._get_loaded_extension_by_name(extension_name)
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
        extension = self._get_loaded_extension_by_name(extension_name)
        func = getattr(extension, "spinup", None)
        if callable(func):
            try:
                func()
            except Exception:
                log.exception("Error while spinning up extension {0!r}"
                              .format(extension_name))
                return

    def _ensure_dependencies_loaded(self, extension_name, forbidden):
        """Ensures that all the dependencies of the given extension are
        loaded.

        When a dependency of the given extension is not loaded yet, it will
        be loaded automatically first.

        Parameters:
            extension_name (str): the name of the extension
            forbidden (List[str]): set of extensions that are already
                being loaded

        Raises:
            ImportError: if an extension cannot be imported
        """
        dependencies = self._get_dependencies_of_extension(extension_name)
        forbidden.append(extension_name)
        for dependency in dependencies:
            self._load(dependency, forbidden)
        forbidden.pop()


class ExtensionAPIProxy(object):
    """Proxy object that allows controlled access to the exported API of
    an extension.

    By default, the proxy object just forwards attribute retrievals as
    dictionary lookups to the API object of the extension, with the
    exception of the ``loaded`` property, which returns ``True`` if the
    extension corresponding to the proxy is loaded and ``False`` otherwise.
    When the extension is not loaded, any attribute retrieval will fail with
    an ``AttributeError`` except the ``loaded`` property.
    """

    def __init__(self, manager, extension_name):
        """Constructor.

        Parameters:
            manager (ExtensionManager): the extension manager that owns the
                proxy.
            extension_name (str): the name of the extension that the proxy
                handles
        """
        self._extension_name = extension_name
        self._manager = manager
        self._manager.loaded.connect(self._on_extension_loaded,
                                     sender=self._manager)
        self._manager.unloaded.connect(self._on_extension_unloaded,
                                       sender=self._manager)

        loaded = self._manager.is_loaded(extension_name)
        if loaded:
            self._api = self._get_api_of_extension(extension_name)
        else:
            self._api = {}
        self._loaded = loaded

    def __getattr__(self, name):
        try:
            return self._api[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def loaded(self):
        """Returns whether the extension represented by the proxy is
        loaded.
        """
        return self._loaded

    def _get_api_of_extension(self, extension_name):
        """Returns the API of the given extension."""
        extension = self._manager._get_loaded_extension_by_name(extension_name)
        api = getattr(extension, "exports", None)
        if api is None:
            api = {}
        elif callable(api):
            api = api()
        if not hasattr(api, "__getitem__"):
            raise TypeError("exports of extension {0!r} must support item "
                            "access with the [] operator")
        return api

    def _on_extension_loaded(self, sender, name, extension):
        """Handler that is called when some extension is loaded into the
        extension manager.
        """
        if name == self._extension_name:
            self._api = self._get_api_of_extension(name)
            self._loaded = True

    def _on_extension_unloaded(self, sender, name, extension):
        """Handler that is called when some extension is unloaded from the
        extension manager.
        """
        if name == self._extension_name:
            self._loaded = False
            self._api = {}
