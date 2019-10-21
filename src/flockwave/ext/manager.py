"""Extension manager class for Flockwave."""

from __future__ import absolute_import, annotations

import importlib

from blinker import Signal
from dataclasses import dataclass, field
from functools import partial
from inspect import iscoroutinefunction
from pkgutil import get_loader
from trio import CancelScope, open_memory_channel, open_nursery, TASK_STATUS_IGNORED
from typing import Any, Dict, Generator, Generic, List, Optional, Set, Type, TypeVar

from flockwave.logger import add_id_to_log, log as base_log, Logger

from .base import Configuration, ExtensionBase
from .utils import bind, cancellable, keydefaultdict

__all__ = ("ExtensionManager",)

EXT_PACKAGE_NAME = __name__.rpartition(".")[0]
base_log = base_log.getChild("manager")

T = TypeVar("T")


class LoadOrder(Generic[T]):
    """Helper object that maintains the order in which extensions were loaded
    so we can unload them in reverse order.
    """

    @dataclass
    class Node:
        data: Any = None
        next: Type["Node"] = None
        prev: Type["Node"] = None

    def __init__(self):
        self._guard = self._tail = LoadOrder.Node()
        self._tail.prev = self._tail.next = self._tail
        self._dict = {}

    def items(self) -> Generator[T, None, None]:
        item = self._guard
        item = item.next
        while item is not self._guard:
            yield item.data
            item = item.next

    def notify_loaded(self, name: T) -> None:
        """Notifies the object that the given extension was loaded."""
        item = self._dict.get(name)
        if not item:
            item = LoadOrder.Node(name)
        else:
            self._unlink_item(item)
        item.prev = self._tail
        item.next = self._guard
        self._tail.next = item
        self._tail = item

    def notify_unloaded(self, name: T) -> None:
        """Notifies the object that the given extension was unloaded."""
        if name not in self._dict:
            return

        item = self._dict.pop(name, None)
        if item:
            return self._unlink_item(item)

    def reversed(self) -> Generator[T, None, None]:
        """Returns a generator that generates items in reversed order compared
        to how they were added.
        """
        item = self._tail
        while item is not self._guard:
            yield item.data
            item = item.prev

    def _unlink_item(self, item: Node) -> None:
        item.prev.next = item.next
        item.next.prev = item.prev


@dataclass
class ExtensionData(object):
    """Data that the extension manager stores related to each extension.

    Attributes:
        name (str): the name of the extension
        api_proxy (object): the API object that the extension exports
        configuration (object): the configuration object of the extension
        dependents (Set[str]): names of other loaded extensions that depend
            on this extension
        instance (object): the loaded instance of the extension
        loaded (bool): whether the extension is loaded
        task (Optional[trio.CancelScope]): a cancellation scope for the
            background task that was spawned for the extension when it
            was loaded, or `None` if no such task was spawned
        worker (Optional[trio.CancelScope]): a cancellation scope for the
            worker task that was spawned for the extension when the first
            client connected to the server, or `None` if no such worker
            was spawned or there are no clients connected
    """

    name: str

    api_proxy: Optional[object] = None
    configuration: Dict[str, Any] = field(default_factory=dict)
    dependents: Set[str] = field(default_factory=set)
    instance: Optional[object] = None
    loaded: bool = False
    log: Logger = None
    task: Optional[CancelScope] = None
    worker: Optional[CancelScope] = None

    @classmethod
    def for_extension(cls, name):
        return cls(name=name, log=base_log.getChild(name))


class ExtensionManager:
    """Central extension manager for a Flockwave application that manages
    the loading, configuration and unloading of extensions.
    """

    loaded = Signal(
        doc="""\
    Signal that is sent by the extension manager when an extension has been
    configured and loaded. The signal has two keyword arguments: ``name`` and
    ``extension``.
    """
    )
    unloaded = Signal(
        doc="""\
    Signal that is sent by the extension manager when an extension has been
    unloaded. The signal has two keyword arguments: ``name`` and ``extension``.
    """
    )

    def __init__(self, package_root=None):
        """Constructor.

        Parameters:
            package_root: the root package in which all other extension
                packages should live
        """
        self._app = None
        self._extensions = keydefaultdict(self._create_extension_data)
        self._extension_package_root = package_root or EXT_PACKAGE_NAME
        self._load_order = LoadOrder()
        self._spinning = False

    @property
    def app(self):
        """The application context of the extension manager. This will also
        be passed on to the extensions when they are initialized.
        """
        return self._app

    async def set_app(self, value):
        """Asynchronous setter for the application context of the
        extension manager.
        """
        if self._app is value:
            return

        if self._spinning:
            await self._spindown_all_extensions()

        self._app = value

        if self._spinning:
            await self._spinup_all_extensions()

    async def _configure(self, configuration: Configuration, **kwds) -> None:
        """Configures the extension manager.

        Extensions that were loaded earlier will be unloaded before loading
        the new ones with the given configuration.

        Parameters:
            configuration: a dictionary mapping names of the
                extensions to their configuration.

        Keyword arguments:
            app: when specified, sets the application context of the
                extension manager as well
        """
        if "app" in kwds:
            await self.set_app(kwds["app"])

        loaded_extensions = set(self.loaded_extensions)

        await self.teardown()

        for extension_name, extension_cfg in configuration.items():
            ext = self._extensions[extension_name]
            ext.configuration = dict(extension_cfg)
            loaded_extensions.add(extension_name)

        for extension_name in sorted(loaded_extensions):
            ext = self._extensions[extension_name]
            enabled = ext.configuration.get("enabled", True)
            if enabled:
                await self.load(extension_name)

    def _create_extension_data(self, extension_name: str) -> None:
        """Creates a helper object holding all data related to the extension
        with the given name.

        Parameters:
            extension_name: the name of the extension

        Raises:
            KeyError: if the extension with the given name does not exist
        """
        if not self.exists(extension_name):
            raise KeyError(extension_name)
        else:
            data = ExtensionData.for_extension(extension_name)
            data.api_proxy = ExtensionAPIProxy(self, extension_name)
            return data

    def _get_loaded_extension_by_name(self, extension_name: str) -> ExtensionBase:
        """Returns the extension object corresponding to the extension
        with the given name if it is loaded.

        Parameters:
            extension_name: the name of the extension

        Returns:
            the extension with the given name

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

    def _get_module_for_extension(self, extension_name: str):
        """Returns the module that contains the given extension.

        Parameters:
            extension_name: the name of the extension

        Returns:
            module: the module containing the extension with the given name
        """
        module_name = self._get_module_name_for_extension(extension_name)
        return importlib.import_module(module_name)

    def _get_module_name_for_extension(self, extension_name: str) -> str:
        """Returns the name of the module that should contain the given
        extension.

        Returns:
            the full, dotted name of the module that should contain the
            extension with the given name
        """
        return "{0}.{1}".format(self._extension_package_root, extension_name)

    def exists(self, extension_name: str) -> bool:
        """Returns whether the extension with the given name exists,
        irrespectively of whether it was loaded already or not.

        Parameters:
            extension_name: the name of the extension

        Returns:
            whether the extension exists
        """
        module_name = self._get_module_name_for_extension(extension_name)
        return get_loader(module_name) is not None

    def import_api(self, extension_name: str) -> ExtensionAPIProxy:
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
            extension_name: the name of the extension whose API is to be
                imported

        Returns:
            a proxy object to the API of the extension that forwards attribute
            retrievals to the API, except for the property named ``loaded``,
            which returns whether the extension is loaded or not.

        Raises:
            KeyError: if the extension with the given name does not exist
        """
        return self._extensions[extension_name].api_proxy

    async def load(self, extension_name: str) -> None:
        """Loads an extension with the given name.

        The extension will be imported from the root extension package
        specified at construction time, or ``flockwave.ext`` if it was not
        specified. When the module contains a callable named ``construct()``,
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
            extension_name: the name of the extension to load

        Returns:
            whatever the `load()` function of the extension returns
        """
        return await self._load(extension_name, forbidden=[])

    @property
    def loaded_extensions(self) -> List[str]:
        """Returns a list containing the names of all the extensions that
        are currently loaded into the extension manager. The caller is free
        to modify the list; it will not affect the extension manager.

        Returns:
            the names of all the extensions that are currently loaded
        """
        return sorted(key for key, ext in self._extensions.items() if ext.loaded)

    def is_loaded(self, extension_name: str) -> bool:
        """Returns whether the given extension is loaded."""
        try:
            self._get_loaded_extension_by_name(extension_name)
            return True
        except KeyError:
            return False

    async def run(
        self, *, configuration: Configuration, app: Any, task_status=TASK_STATUS_IGNORED
    ) -> None:
        """Asynchronous task that runs the exception manager itself.

        This task simply waits for messages that request certain tasks managed
        by the extensions to be started. It also takes care of catching
        exceptions from the managed tasks and logging them without crashing the
        entire application.
        """
        try:
            self._task_queue, task_queue_rx = open_memory_channel(1024)

            await self._configure(configuration, app=app)

            async with open_nursery() as nursery:
                task_status.started()
                async for func, args, scope in task_queue_rx:
                    if scope is not None:
                        func = partial(func, cancel_scope=scope)
                    nursery.start_soon(func, *args)

        finally:
            self._task_queue = None

    async def _run_in_background(self, func, *args, cancellable=False):
        """Runs the given function as a background task in the extension
        manager.

        Blocks until the task is started.
        """
        scope = CancelScope() if cancellable or hasattr(func, "_cancellable") else None
        await self._task_queue.send((func, args, scope))
        return scope

    @property
    def spinning(self) -> bool:
        """Whether the extensions in the extension manager are "spinning".

        This property is relevant for extensions that can exist in an idle
        state and in a "spinning" state. Setting the property to `True` will
        put all such extensions in the "spinning" state by invoking the
        `spinup()` method of the extensions. Setting the property to `False`
        will put all such extensions in the "idle" state by invoking the
        `spindown()` method of the extensions. Additionally, the `worker()`
        task of the extension will be running only if it is in the "spinning"
        state.
        """
        return self._spinning

    async def set_spinning(self, value: bool) -> None:
        """Asynchronous setter for the `spinning` property."""
        value = bool(value)

        if self._spinning == value:
            return

        if self._spinning:
            await self._spindown_all_extensions()

        self._spinning = value

        if self._spinning:
            await self._spinup_all_extensions()

    async def teardown(self) -> None:
        """Tears down the extension manager and prepares it for destruction."""
        for ext_name in self._load_order.reversed():
            await self.unload(ext_name)

    async def unload(self, extension_name: str) -> None:
        """Unloads the extension with the given name.

        Parameters:
            extension_name: the name of the extension to unload
        """
        log = add_id_to_log(base_log, id=extension_name)

        # Get the extension instance
        try:
            extension = self._get_loaded_extension_by_name(extension_name)
        except KeyError:
            log.warning("Tried to unload extension but it is not loaded")
            return

        # Get the associated internal bookkeeping object of the extension
        extension_data = self._extensions[extension_name]
        if extension_data.dependents:
            message = "Failed to unload extension {0!r} because it is still in use".format(
                extension_name
            )
            raise RuntimeError(message)

        # Spin down the extension if needed
        if self._spinning:
            await self._spindown_extension(extension_name)

        # Stop the task associated to the extension if it has one
        if extension_data.task:
            # TODO(ntamas): wait until the task is cancelled
            extension_data.task.cancel()
            extension_data.task = None

        # Unload the extension
        clean_unload = True

        func = getattr(extension, "unload", None)
        if callable(func):
            try:
                func(self.app)
            except Exception:
                clean_unload = False
                log.exception("Error while unloading extension; " "forcing unload")

        # Update the internal bookkeeping object
        extension_data.loaded = False
        extension_data.instance = None

        # Remove the extension from its dependents
        self._load_order.notify_unloaded(extension_name)

        for dependency in self._get_dependencies_of_extension(extension_name):
            self._extensions[dependency].dependents.remove(extension_name)

        # Send a signal that the extension was unloaded
        self.unloaded.send(self, name=extension_name, extension=extension)

        # Add a log message
        if clean_unload:
            log.debug("Unloaded extension")
        else:
            log.warning("Unloaded extension")

    def _get_dependencies_of_extension(self, extension_name: str) -> Set[str]:
        """Determines the list of extensions that a given extension depends
        on directly.

        Parameters:
            extension_name: the name of the extension

        Returns:
            the names of the extensions that the given extension depends on
        """
        try:
            module = self._get_module_for_extension(extension_name)
        except ImportError:
            base_log.exception(
                "Error while importing extension {0!r}".format(extension_name)
            )
            raise

        func = getattr(module, "get_dependencies", None)
        if callable(func):
            try:
                dependencies = func()
            except Exception:
                base_log.exception(
                    "Error while determining dependencies of "
                    "extension {0!r}".format(extension_name)
                )
                dependencies = None
        else:
            dependencies = getattr(module, "dependencies", None)

        return set(dependencies or [])

    async def _load(self, extension_name: str, forbidden: List[str]):
        if extension_name in forbidden:
            cycle = forbidden + [extension_name]
            base_log.error(
                "Dependency cycle detected: {0}".format(" -> ".join(map(str, cycle)))
            )
            return

        await self._ensure_dependencies_loaded(extension_name, forbidden)
        if not self.is_loaded(extension_name):
            return await self._load_single_extension(extension_name)

    async def _load_single_extension(self, extension_name: str):
        """Loads an extension with the given name, assuming that all its
        dependencies are already loaded.

        This function is internal; use `load()` instead if you want to load
        an extension programmatically, and it will take care of loading all
        the dependencies as well.

        Parameters:
            extension_name: the name of the extension to load
        """
        if extension_name in ("logger", "manager", "base", "__init__"):
            raise ValueError("invalid extension name: {0!r}".format(extension_name))

        log = add_id_to_log(base_log, id=extension_name)

        extension_data = self._extensions[extension_name]
        configuration = extension_data.configuration

        log.debug("Loading extension")
        try:
            module = self._get_module_for_extension(extension_name)
        except ImportError:
            log.exception("Error while importing extension")
            return None

        instance_factory = getattr(module, "construct", None)

        try:
            extension = instance_factory() if instance_factory else module
        except Exception:
            log.exception("Error while instantiating extension")
            return None

        args = (self.app, configuration, extension_data.log)

        func = getattr(extension, "load", None)
        if callable(func):
            try:
                result = bind(func, args, partial=True)()
            except Exception:
                log.exception("Error while loading extension")
                return None
        else:
            result = None

        task = getattr(extension, "run", None)
        if iscoroutinefunction(task):
            extension_data.task = await self._run_in_background(
                cancellable(bind(task, args, partial=True))
            )
        elif task is not None:
            log.warn("run() must be an async function")

        extension_data.instance = extension
        extension_data.loaded = True
        self._load_order.notify_loaded(extension_name)

        for dependency in self._get_dependencies_of_extension(extension_name):
            self._extensions[dependency].dependents.add(extension_name)

        self.loaded.send(self, name=extension_name, extension=extension)

        if self._spinning:
            await self._spinup_extension(extension)

        return result

    async def _spindown_all_extensions(self) -> None:
        """Iterates over all loaded extensions and spins down each one of
        them.
        """
        for extension_name in self._load_order.reversed():
            await self._spindown_extension(extension_name)

    async def _spinup_all_extensions(self) -> None:
        """Iterates over all loaded extensions and spins up each one of
        them.
        """
        for extension_name in self._load_order.items():
            await self._spinup_extension(extension_name)

    async def _spindown_extension(self, extension_name: str) -> None:
        """Spins down the given extension.

        This is done by calling the ``spindown()`` method or function of
        the extension, if any.

        Arguments:
            extension_name: the name of the extension to spin down.
        """
        extension = self._get_loaded_extension_by_name(extension_name)
        extension_data = self._extensions[extension_name]

        log = add_id_to_log(base_log, id=extension_name)

        # Stop the worker associated to the extension if it has one
        if extension_data.worker:
            # TODO(ntamas): wait until the worker is cancelled
            extension_data.worker.cancel()
            extension_data.worker = None

        # Call the spindown hook of the extension if it has one
        func = getattr(extension, "spindown", None)
        if callable(func):
            try:
                func()
            except Exception:
                log.exception("Error while spinning down extension")
                return

    async def _spinup_extension(self, extension_name: str) -> None:
        """Spins up the given extension.

        This is done by calling the ``spinup()`` method or function of
        the extension, if any.

        Arguments:
            extension_name: the name of the extension to spin up.
        """
        extension = self._get_loaded_extension_by_name(extension_name)
        extension_data = self._extensions[extension_name]

        log = add_id_to_log(base_log, id=extension_name)

        # Call the spinup hook of the extension if it has one
        func = getattr(extension, "spinup", None)
        if callable(func):
            try:
                func()
            except Exception:
                log.exception("Error while spinning up extension")
                return

        # Start the worker associated to the extension if it has one
        task = getattr(extension, "worker", None)
        if iscoroutinefunction(task):
            args = (self.app, extension_data.configuration, extension_data.log)
            self._extensions[extension_name].worker = await self._run_in_background(
                cancellable(bind(task, args, partial=True))
            )

    async def _ensure_dependencies_loaded(
        self, extension_name: str, forbidden: List[str]
    ):
        """Ensures that all the dependencies of the given extension are
        loaded.

        When a dependency of the given extension is not loaded yet, it will
        be loaded automatically first.

        Parameters:
            extension_name: the name of the extension
            forbidden: set of extensions that are already being loaded

        Raises:
            ImportError: if an extension cannot be imported
        """
        dependencies = self._get_dependencies_of_extension(extension_name)
        forbidden.append(extension_name)
        for dependency in dependencies:
            await self._load(dependency, forbidden)
        forbidden.pop()


class ExtensionAPIProxy:
    """Proxy object that allows controlled access to the exported API of
    an extension.

    By default, the proxy object just forwards attribute retrievals as
    dictionary lookups to the API object of the extension, with the
    exception of the ``loaded`` property, which returns ``True`` if the
    extension corresponding to the proxy is loaded and ``False`` otherwise.
    When the extension is not loaded, any attribute retrieval will fail with
    an ``AttributeError`` except the ``loaded`` property.
    """

    def __init__(self, manager: ExtensionManager, extension_name: str):
        """Constructor.

        Parameters:
            manager: the extension manager that owns the proxy.
            extension_name: the name of the extension that the proxy handles
        """
        self._extension_name = extension_name
        self._manager = manager
        self._manager.loaded.connect(self._on_extension_loaded, sender=self._manager)
        self._manager.unloaded.connect(
            self._on_extension_unloaded, sender=self._manager
        )

        loaded = self._manager.is_loaded(extension_name)
        self._api = self._get_api_of_extension(extension_name) if loaded else {}
        self._loaded = loaded

    def __getattr__(self, name: str):
        try:
            return self._api[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def loaded(self) -> bool:
        """Returns whether the extension represented by the proxy is
        loaded.
        """
        return self._loaded

    def _get_api_of_extension(self, extension_name: str):
        """Returns the API of the given extension."""
        extension = self._manager._get_loaded_extension_by_name(extension_name)
        api = getattr(extension, "exports", None)
        if api is None:
            api = {}
        elif callable(api):
            api = api()
        if not hasattr(api, "__getitem__"):
            raise TypeError(
                "exports of extension {0!r} must support item "
                "access with the [] operator"
            ).format(extension_name)
        return api

    def _on_extension_loaded(self, sender, name: str, extension: ExtensionBase) -> None:
        """Handler that is called when some extension is loaded into the
        extension manager.
        """
        if name == self._extension_name:
            self._api = self._get_api_of_extension(name)
            self._loaded = True

    def _on_extension_unloaded(
        self, sender, name: str, extension: ExtensionBase
    ) -> None:
        """Handler that is called when some extension is unloaded from the
        extension manager.
        """
        if name == self._extension_name:
            self._loaded = False
            self._api = {}
