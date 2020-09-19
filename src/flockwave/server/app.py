"""Application object for the Skybrush server."""

from appdirs import AppDirs
from blinker import Signal
from collections import defaultdict
from functools import partial
from inspect import isawaitable
from time import time
from trio import (
    BrokenResourceError,
    CancelScope,
    MultiError,
    move_on_after,
    open_memory_channel,
    open_nursery,
)
from typing import Callable, Dict, List, Optional

from flockwave.connections import (
    ConnectionSupervisor,
    ConnectionTask,
    SupervisionPolicy,
)
from flockwave.ext.manager import ExtensionAPIProxy, ExtensionManager
from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.utils import divide_by

from .commands import CommandExecutionManager
from .configurator import AppConfigurator
from .errors import NotSupportedError
from .logger import log
from .message_hub import (
    ConnectionStatusMessageRateLimiter,
    GenericRateLimiter,
    MessageHub,
    RateLimiters,
)
from .model.client import Client
from .model.devices import DeviceTree, DeviceTreeSubscriptionManager
from .model.errors import ClientNotSubscribedError, NoSuchPathError
from .model.messages import FlockwaveMessage, FlockwaveResponse
from .model.object import ModelObject
from .model.uav import is_uav, UAV, UAVDriver
from .model.world import World
from .registries import (
    ChannelTypeRegistry,
    ClientRegistry,
    ConnectionRegistry,
    ConnectionRegistryEntry,
    ObjectRegistry,
    find_in_registry,
)
from .version import __version__ as server_version

__all__ = ("app",)

PACKAGE_NAME = __name__.rpartition(".")[0]


class SkybrushServer:
    """Main application object for the Flockwave server.

    Attributes:
        channel_type_registry (ChannelTypeRegistry): central registry for
            types of communication channels that the server can handle and
            manage. Types of communication channels include Socket.IO
            streams, TCP or UDP sockets and so on.
        client_registry (ClientRegistry): central registry for the clients
            that are currently connected to the server
        command_execution_manager (CommandExecutionManager): object that
            manages the asynchronous execution of commands on remote UAVs
            (i.e. commands that cannot be executed immediately in a
            synchronous manner)
        config (dict): dictionary holding the configuration options of the
            application
        debug (bool): boolean flag to denote whether the application is in
            debugging mode
        device_tree (DeviceTree): a tree-like data structure that contains
            a first-level node for every UAV and then contains additional
            nodes in each UAV subtree for the devices and channels of the
            UAV
        extension_manager (ExtensionManager): object that manages the
            loading and unloading of server extensions
        message_hub (MessageHub): central messaging hub via which one can
            send Flockwave messages
        object_registry (ObjectRegistry): central registry for the objects
            known to the server
        world (World): a representation of the "world" in which the flock
            of UAVs live. By default, the world is empty but extensions may
            extend it with objects.

    Private attributes:
        _starting (Signal): signal that is emitted when the server has finished
            parsing the configuration and loading the extensions, and is about
            to enter the main loop. This signal can be used by extensions to
            hook into the startup process.
        _stopping (Signal): signal that is emitted when the server is about to
            shut down its main loop. This signal can be used by extensions to
            hook into the shutdown process.
    """

    _starting = Signal()
    _stopping = Signal()

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
        # Placeholder for a nursery that parents all tasks in the server.
        # This will be set to a real nursery when the server starts
        self._nursery = None

        # Create a Trio task queue that will be used by other components of the
        # application to schedule background tasks to be executed in the main
        # Trio nursery.
        # TODO(ntamas): not sure if this is going to be needed in the end; we
        # might just as well remove it
        self._task_queue = open_memory_channel(32)

        # Create an object that can be used to get hold of commonly used
        # directories within the app
        self.dirs = AppDirs("Skybrush Server", "CollMot Robotics")

        # Create an object to hold information about all the registered
        # communication channel types that the server can handle
        self.channel_type_registry = ChannelTypeRegistry()

        # Create an object to hold information about all the connected
        # clients that the server can talk to
        self.client_registry = ClientRegistry()
        self.client_registry.channel_type_registry = self.channel_type_registry
        self.client_registry.count_changed.connect(
            self._on_client_count_changed, sender=self.client_registry
        )

        # Create an object that keeps track of commands being executed
        # asynchronously on remote UAVs
        self.command_execution_manager = CommandExecutionManager()
        self.command_execution_manager.expired.connect(
            self._on_command_execution_timeout, sender=self.command_execution_manager
        )
        self.command_execution_manager.finished.connect(
            self._on_command_execution_finished, sender=self.command_execution_manager
        )

        # Creates an object whose responsibility is to restart connections
        # that closed unexpectedly
        self.connection_supervisor = ConnectionSupervisor()

        # Creates an object to hold information about all the connections
        # to external data sources that the server manages
        self.connection_registry = ConnectionRegistry()
        self.connection_registry.connection_state_changed.connect(
            self._on_connection_state_changed, sender=self.connection_registry
        )
        self.connection_registry.added.connect(
            self._on_connection_added, sender=self.connection_registry
        )
        self.connection_registry.removed.connect(
            self._on_connection_removed, sender=self.connection_registry
        )

        # Create the extension manager of the application
        self.extension_manager = ExtensionManager(PACKAGE_NAME + ".ext")

        # Create a message hub that will handle incoming and outgoing
        # messages
        self.message_hub = MessageHub()
        self.message_hub.channel_type_registry = self.channel_type_registry
        self.message_hub.client_registry = self.client_registry

        # Create an object that manages rate-limiting for specific types of
        # messages
        self.rate_limiters = RateLimiters(dispatcher=self.message_hub.send_message)
        self.rate_limiters.register(
            "CONN-INF",
            ConnectionStatusMessageRateLimiter(self.create_CONN_INF_message_for),
        )
        self.rate_limiters.register(
            "UAV-INF", GenericRateLimiter(self.create_UAV_INF_message_for)
        )

        # Create an object to hold information about all the objects that
        # the server knows about
        self.object_registry = ObjectRegistry()
        self.object_registry.removed.connect(
            self._on_object_removed, sender=self.object_registry
        )

        # Create the global world object
        self.world = World()

        # Create a global device tree and ensure that new UAVs are
        # registered in it
        self.device_tree = DeviceTree()
        self.device_tree.object_registry = self.object_registry

        # Create an object to manage the associations between clients and
        # the device tree paths that the clients are subscribed to
        self.device_tree_subscriptions = DeviceTreeSubscriptionManager(self.device_tree)
        self.device_tree_subscriptions.client_registry = self.client_registry
        self.device_tree_subscriptions.message_hub = self.message_hub

    def create_CONN_INF_message_for(self, connection_ids, in_response_to=None):
        """Creates a CONN-INF message that contains information regarding
        the connections with the given IDs.

        Parameters:
            connection_ids (iterable): list of connection IDs
            in_response_to (FlockwaveMessage or None): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the CONN-INF message with the status info of
                the given connections
        """
        statuses = {}

        body = {"status": statuses, "type": "CONN-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for connection_id in connection_ids:
            entry = self._find_connection_by_id(connection_id, response)
            if entry:
                statuses[connection_id] = entry.json

        return response

    def create_DEV_INF_message_for(self, paths, in_response_to=None):
        """Creates a DEV-INF message that contains information regarding
        the current values of the channels in the subtrees of the device
        tree matched by the given device tree paths.

        Parameters:
            paths (iterable): list of device tree paths
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the DEV-INF message with the current values of
                the channels in the subtrees matched by the given device
                tree paths
        """
        return self.device_tree_subscriptions.create_DEV_INF_message_for(
            paths, in_response_to
        )

    def create_DEV_LIST_message_for(self, object_ids, in_response_to=None):
        """Creates a DEV-LIST message that contains information regarding
        the device trees of the objects with the given IDs.

        Parameters:
            object_ids (iterable): list of object IDs
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the DEV-LIST message with the device trees of
                the given objects
        """
        devices = {}

        body = {"devices": devices, "type": "DEV-LIST"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for object_id in object_ids:
            object = self._find_object_by_id(object_id, response)
            if object:
                if object.device_tree_node:
                    devices[object_id] = object.device_tree_node.json
                else:
                    devices[object_id] = {}

        return response

    def create_DEV_LISTSUB_message_for(self, client, path_filter, in_response_to=None):
        """Creates a DEV-LISTSUB message that contains information about the
        device tree paths that the given client is subscribed to.

        Parameters:
            client (Client): the client whose subscriptions we are
                interested in
            path_filter (iterable): list of device tree paths whose subtrees
                the client is interested in
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the DEV-LISTSUB message with the subscriptions
                of the client that match the path filters
        """
        manager = self.device_tree_subscriptions
        subscriptions = manager.list_subscriptions(client, path_filter)

        body = {"paths": list(subscriptions.elements()), "type": "DEV-LISTSUB"}

        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        return response

    def create_DEV_SUB_message_for(self, client, paths, in_response_to):
        """Creates a DEV-SUB response for the given message and subscribes
        the given client to the given paths.

        Parameters:
            client (Client): the client to subscribe to the given paths
            paths (iterable): list of device tree paths to subscribe the
                client to
            in_response_to (FlockwaveMessage): the message that the
                constructed message will respond to.

        Returns:
            FlockwaveMessage: the DEV-SUB message with the paths that the
                client was subscribed to, along with error messages for the
                paths that the client was not subscribed to
        """
        manager = self.device_tree_subscriptions
        response = self.message_hub.create_response_or_notification(
            {}, in_response_to=in_response_to
        )

        for path in paths:
            try:
                manager.subscribe(client, path)
            except NoSuchPathError:
                response.add_error(path, "No such device tree path")
            else:
                response.add_success(path)

        return response

    def create_DEV_UNSUB_message_for(
        self, client, paths, in_response_to, remove_all, include_subtrees
    ):
        """Creates a DEV-UNSUB response for the given message and
        unsubscribes the given client from the given paths.

        Parameters:
            client (Client): the client to unsubscribe from the given paths
            paths (iterable): list of device tree paths to unsubscribe the
                given client from
            in_response_to (FlockwaveMessage): the message that the
                constructed message will respond to.
            remove_all (bool): when ``True``, the client will be unsubscribed
                from the given paths no matter how many times it is
                subscribed to them. When ``False``, an unsubscription will
                decrease the number of subscriptions to the given path by
                1 only.
            include_subtrees (bool): when ``True``, subscriptions to nodes
                that are in the subtrees of the given paths will also be
                removed

        Returns:
            FlockwaveMessage: the DEV-UNSUB message with the paths that the
                client was unsubscribed from, along with error messages for
                the paths that the client was not unsubscribed from
        """
        manager = self.device_tree_subscriptions
        response = self.message_hub.create_response_or_notification(
            {}, in_response_to=in_response_to
        )

        if include_subtrees:
            # Collect all the subscriptions from the subtrees and pretend
            # that the user submitted that
            paths = manager.list_subscriptions(client, paths)

        for path in paths:
            try:
                manager.unsubscribe(client, path, force=remove_all)
            except NoSuchPathError:
                response.add_error(path, "No such device tree path")
            except ClientNotSubscribedError:
                response.add_error(path, "Not subscribed to this path")
            else:
                response.add_success(path)

        return response

    def create_UAV_INF_message_for(self, uav_ids, in_response_to=None):
        """Creates an UAV-INF message that contains information regarding
        the UAVs with the given IDs.

        Typically, you should not use this method from extensions because
        it allows one to bypass the built-in rate limiting for UAV-INF
        messages. The only exception is when ``in_response_to`` is set to
        a certain message identifier, in which case it makes sense to send
        the UAV-INF response immediately (after all, it was requested
        explicitly). If you only want to broadcast UAV-INF messages to all
        interested parties, use ``request_to_send_UAV_INF_message_for()``
        instead, which will send the notification immediately if the rate
        limiting constraints allow, but it may also wait a bit if the
        UAV-INF messages are sent too frequently.

        Parameters:
            uav_ids (iterable): list of UAV IDs
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the UAV-INF message with the status info of
                the given UAVs
        """
        statuses = {}

        body = {"status": statuses, "type": "UAV-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for uav_id in uav_ids:
            uav = self.find_uav_by_id(uav_id, response)
            if uav:
                statuses[uav_id] = uav.status.json

        return response

    async def disconnect_client(
        self, client: Client, reason: str = None, timeout: float = 10
    ) -> None:
        """Disconnects the given client from the server.

        Parameters:
            client: the client to disconnect
            reason: the reason for disconnection. WHen it is not ``None``,
                a ``SYS-CLOSE`` message is sent to the client before the
                connection is closed.
            timeout: maximum number of seconds to wait for the disconnection
                to happen gracefully. A forceful disconnection is attempted
                if the timeout expires.
        """
        if not client.channel:
            return

        if reason:
            message = self.message_hub.create_notification(
                body={"type": "SYS-CLOSE", "reason": reason}
            )
        else:
            message = None

        with move_on_after(timeout) as cancel_scope:
            if message:
                request = await self.message_hub.send_message(message, to=client)
                await request.wait_until_sent()
            await client.channel.close()

        if cancel_scope.cancelled_caught:
            await client.channel.close(force=True)

    async def dispatch_to_uavs(
        self, message: FlockwaveMessage, sender: Client
    ) -> FlockwaveMessage:
        """Dispatches a message intended for multiple UAVs to the appropriate
        UAV drivers.

        Parameters:
            message: the message that contains a request that is to be forwarded
                to multiple UAVs. The message is expected to have an ``ids``
                property that lists the UAVs to dispatch the message to.
            sender: the client that sent the message

        Returns:
            a response to the original message that lists the IDs of the UAVs
            for which the message has been sent successfully and also the IDs of
            the UAVs for which the dispatch failed (in the ``success`` and
            ``failure`` keys).
        """
        cmd_manager = self.command_execution_manager

        # Create the response
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=message
        )

        # Process the body
        parameters = dict(message.body)
        message_type = parameters.pop("type")
        uav_ids = parameters.pop("ids")

        # Sort the UAVs being targeted by drivers
        uavs_by_drivers = self.sort_uavs_by_drivers(uav_ids, response)

        # Find the method to invoke on the driver
        method_name, transformer = {
            "OBJ-CMD": ("send_command", None),
            "UAV-FLY": (
                "send_fly_to_target_signal",
                {"target": GPSCoordinate.from_json},
            ),
            "UAV-HALT": ("send_shutdown_signal", None),
            "UAV-LAND": ("send_landing_signal", None),
            "UAV-MOTOR": ("send_motor_start_stop_signal", None),
            "UAV-RST": ("send_reset_signal", None),
            "UAV-RTH": ("send_return_to_home_signal", None),
            "UAV-SIGNAL": (
                "send_light_or_sound_emission_signal",
                {"duration": divide_by(1000)},
            ),
            "UAV-TAKEOFF": ("send_takeoff_signal", None),
            "UAV-VER": ("request_version_info", None),
        }.get(message_type, (None, None))

        # Transform the incoming arguments if needed before sending them
        # to the driver method
        if transformer is not None:
            if callable(transformer):
                parameters = transformer(parameters)
            else:
                for parameter_name, transformer in transformer.items():
                    if parameter_name in parameters:
                        value = parameters[parameter_name]
                        parameters[parameter_name] = transformer(value)

        # Ask each affected driver to send the message to the UAV
        for driver, uavs in uavs_by_drivers.items():
            # Look up the method in the driver
            common_error, results = None, None
            try:
                method = getattr(driver, method_name)
            except (AttributeError, RuntimeError, TypeError):
                common_error = "Operation not supported"
                method = None

            # Execute the method and catch all runtime errors
            if method is not None:
                try:
                    results = method(uavs, **parameters)
                except NotImplementedError:
                    common_error = "Operation not implemented"
                except NotSupportedError:
                    common_error = "Operation not supported"
                except Exception as ex:
                    common_error = "Unexpected error: {0}".format(ex)
                    log.exception(ex)

            # Update the response
            if common_error is not None:
                for uav in uavs:
                    response.add_error(uav.id, common_error)
            else:
                if isawaitable(results):
                    # Results are produced by an async function; we have to wait
                    # for it
                    # TODO(ntamas): no, we don't have to wait for it; we have
                    # to create a receipt for each UAV and then send a response
                    # now
                    try:
                        results = await results
                    except RuntimeError as ex:
                        # this is probably okay
                        results = ex
                    except Exception as ex:
                        # this is unexpected; let's log it
                        results = ex
                        log.exception(ex)

                if isinstance(results, Exception):
                    # Received an exception; send it back for all UAVs
                    for uav in uavs:
                        response.add_error(uav.id, str(results))
                elif not isinstance(results, dict):
                    # Common result has arrived, send it back for all UAVs
                    for uav in uavs:
                        response.add_result(uav.id, results)
                else:
                    # Results have arrived for each UAV individually, process them
                    for uav, result in results.items():
                        if isinstance(result, Exception):
                            response.add_error(uav.id, str(result))
                        elif isawaitable(result):
                            receipt = await cmd_manager.new(
                                result, client_to_notify=sender.id
                            )
                            response.add_receipt(uav.id, receipt)
                            response.when_sent(
                                cmd_manager.mark_as_clients_notified, receipt.id
                            )
                        else:
                            response.add_result(uav.id, result)

        return response

    def find_uav_by_id(
        self, uav_id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[UAV]:
        """Finds the UAV with the given ID in the object registry or registers
        a failure in the given response object if there is no UAV with the
        given ID.

        Parameters:
            uav_id: the ID of the UAV to find
            response: the response in which
                the failure can be registered

        Returns:
            the UAV with the given ID or ``None`` if there is no such UAV
        """
        return find_in_registry(
            self.object_registry,
            uav_id,
            predicate=is_uav,
            response=response,
            failure_reason="No such UAV",
        )

    def import_api(self, extension_name: str) -> ExtensionAPIProxy:
        """Imports the API exposed by an extension.

        Extensions *may* have a dictionary named ``exports`` that allows the
        extension to export some of its variables, functions or methods.
        Other extensions may access the exported members of an extension by
        calling the `import_api`_ method of the application.

        This function supports "lazy imports", i.e. one may import the API
        of an extension before loading the extension. When the extension
        is not loaded, the returned API object will have a single property
        named ``loaded`` that is set to ``False``. When the extension is
        loaded, the returned API object will set ``loaded`` to ``True``.
        Attribute retrievals on the returned API object are forwarded to the
        API of the extension.

        Parameters:
            extension_name: the name of the extension whose API is to
                be imported

        Returns:
            ExtensionAPIProxy: a proxy object to the API of the extension
                that forwards attribute retrievals to the API, except for
                the property named ``loaded``, which returns whether the
                extension is loaded or not.

        Raises:
            KeyError: if the extension with the given name does not exist
        """
        return self.extension_manager.import_api(extension_name)

    @property
    def num_clients(self):
        """The number of clients connected to the server."""
        return self.client_registry.num_entries

    def prepare(self, config: Optional[str], debug: bool = False) -> Optional[int]:
        """Hook function that contains preparation steps that should be
        performed by the server before it starts serving requests.

        Parameters:
            config: name of the configuration file to load
            debug: whether to force the app into debug mode

        Returns:
            error code to terminate the app with if the preparation was not
            successful; ``None`` if the preparation was successful
        """
        configurator = AppConfigurator(
            self.config,
            environment_variable="SKYBRUSH_SETTINGS",
            default_filename="skybrush.cfg",
            log=log,
            package_name=PACKAGE_NAME,
        )
        if not configurator.configure(config):
            return 1

        if debug or self.config.get("DEBUG"):
            self.debug = True

        # Process the configuration options
        cfg = self.config.get("COMMAND_EXECUTION_MANAGER", {})
        self.command_execution_manager.timeout = cfg.get("timeout", 30)

    def register_startup_hook(self, func: Callable[[object], None]):
        """Registers a function that will be called when the application is
        starting up.

        Parameters:
            func: the function to call. It will be called with the application
                instance as its only argument.
        """
        self._starting.connect(func, sender=self)

    def register_shutdown_hook(self, func: Callable[[object], None]):
        """Registers a function that will be called when the application is
        shutting down.

        Parameters:
            func: the function to call. It will be called with the application
                instance as its only argument.
        """
        self._stopping.connect(func, sender=self)

    def request_to_send_UAV_INF_message_for(self, uav_ids):
        """Requests the application to send an UAV-INF message that contains
        information regarding the UAVs with the given IDs. The application
        may send the message immediately or opt to delay it a bit in order
        to ensure that UAV-INF notifications are not emitted too frequently.

        Parameters:
            uav_ids (iterable): list of UAV IDs
        """
        self.rate_limiters.request_to_send("UAV-INF", uav_ids)

    async def run(self) -> None:
        # Helper function to ignore KeyboardInterrupt exceptions even if
        # they are wrapped in a Trio MultiError
        def ignore_keyboard_interrupt(exc):
            return None if isinstance(exc, KeyboardInterrupt) else exc

        # Load the configuration
        extension_config = self.config.get("EXTENSIONS", {})

        # Force-load the ext_manager extension
        extension_config["ext_manager"] = {}

        try:
            with MultiError.catch(ignore_keyboard_interrupt):
                self._starting.send(self)
                async with open_nursery() as nursery:
                    self._nursery = nursery

                    await nursery.start(
                        partial(
                            self.extension_manager.run,
                            configuration=extension_config,
                            app=self,
                        )
                    )

                    nursery.start_soon(self.connection_supervisor.run)
                    nursery.start_soon(self.command_execution_manager.run)
                    nursery.start_soon(self.message_hub.run)
                    nursery.start_soon(self.rate_limiters.run)

                    async for func, args, scope in self._task_queue[1]:
                        if scope is not None:
                            func = partial(func, cancel_scope=scope)
                        nursery.start_soon(func, *args)

        finally:
            self._nursery = None
            await self.teardown()

    def request_shutdown(self):
        """Requests tha application to shut down in a clean way.

        Has no effect if the main nursery of the app is not running.
        """
        if self._nursery:
            self._nursery.cancel_scope.cancel()

    def run_in_background(self, func, *args, cancellable=False):
        """Runs the given function as a background task in the application."""
        scope = CancelScope() if cancellable or hasattr(func, "_cancellable") else None
        self._task_queue[0].send_nowait((func, args, scope))
        return scope

    def sort_uavs_by_drivers(
        self, uav_ids: List[str], response: Optional[FlockwaveResponse] = None
    ) -> Dict[UAVDriver, List[UAV]]:
        """Given a list of UAV IDs, returns a mapping that maps UAV drivers
        to the UAVs specified by the IDs.

        Parameters:
            uav_ids: list of UAV IDs
            response: optional response in which UAV lookup failures can be
                registered

        Returns:
            mapping of UAV drivers to the UAVs that were selected by the given UAV IDs
        """
        result = defaultdict(list)
        for uav_id in uav_ids:
            uav = self.find_uav_by_id(uav_id, response)
            if uav:
                result[uav.driver].append(uav)
        return result

    async def supervise(
        self,
        connection,
        *,
        task: Optional[ConnectionTask] = None,
        policy: Optional[SupervisionPolicy] = None
    ):
        """Shorthand to `self.connection_supervisor.supervise()`. See the
        details there.
        """
        await self.connection_supervisor.supervise(connection, task=task, policy=policy)

    async def teardown(self):
        """Called when the application is about to shut down. Calls all
        registered shutdown hooks and performs additional cleanup if needed.
        """
        self._stopping.send(self)
        await self.extension_manager.teardown()

    def unregister_startup_hook(self, func: Callable[[object], None]):
        """Unregisters a function that would have been called when the
        application is starting up.

        Parameters:
            func: the function to unregister.
        """
        self._starting.disconnect(func, sender=self)

    def unregister_shutdown_hook(self, func: Callable[[object], None]):
        """Unregisters a function that would have been called when the
        application is shutting down.

        Parameters:
            func: the function to unregister.
        """
        self._stopping.disconnect(func, sender=self)

    @property
    def version(self) -> str:
        """The version number of the server application."""
        return server_version

    def _find_command_receipt_by_id(self, receipt_id, response=None):
        """Finds the asynchronous command execution receipt with the given
        ID in the command execution manager or registers a failure in the
        given response object if there is no command being executed with the
        given ID.

        Parameters:
            receipt_id (str): the ID of the receipt to find
            response (Optional[FlockwaveResponse]): the response in which
                the failure can be registered

        Returns:
            Optional[CommandExecutionStatus]: the status object for the
                execution of the asynchronous command with the given ID
                or ``None`` if there is no such command
        """
        return find_in_registry(
            self.command_execution_manager,
            receipt_id,
            response=response,
            failure_reason="No such receipt",
        )

    def _find_connection_by_id(self, connection_id, response=None):
        """Finds the connection with the given ID in the connection registry
        or registers a failure in the given response object if there is no
        connection with the given ID.

        Parameters:
            connection_id (str): the ID of the connection to find
            response (Optional[FlockwaveResponse]): the response in which
                the failure can be registered

        Returns:
            Optional[ConnectionRegistryEntry]: the entry in the connection
                registry with the given ID or ``None`` if there is no such
                connection
        """
        return find_in_registry(
            self.connection_registry,
            connection_id,
            response=response,
            failure_reason="No such connection",
        )

    def _find_object_by_id(
        self, object_id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[ModelObject]:
        """Finds the object with the given ID in the object registry or registers
        a failure in the given response object if there is no object with the
        given ID.

        Parameters:
            object_id: the ID of the UAV to find
            response: the response in which the failure can be registered

        Returns:
            the object with the given ID or ``None`` if there is no such object
        """
        return find_in_registry(
            self.object_registry,
            object_id,
            response=response,
            failure_reason="No such object",
        )

    def _on_client_count_changed(self, sender):
        """Handler called when the number of clients attached to the server
        has changed.
        """
        self.run_in_background(
            self.extension_manager.set_spinning, self.num_clients > 0
        )

    def _on_connection_state_changed(self, sender, entry, old_state, new_state):
        """Handler called when the state of a connection changes somewhere
        within the server. Dispatches an appropriate ``CONN-INF`` message.

        Parameters:
            sender (ConnectionRegistry): the connection registry
            entry (ConnectionEntry): a connection entry from the connection
                registry
            old_state (ConnectionState): the old state of the connection
            new_state (ConnectionState): the old state of the connection
        """
        self.rate_limiters.request_to_send("CONN-INF", entry.id, old_state, new_state)

    def _on_command_execution_finished(self, sender, status):
        """Handler called when the execution of a remote asynchronous
        command finished. Dispatches an appropriate ``ASYNC-RESP`` message.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
            status (CommandExecutionStatus): the status object corresponding
                to the command whose execution has just finished.
        """
        body = {"type": "ASYNC-RESP", "id": status.id}

        if status.error:
            body["error"] = (
                str(status.error)
                if not hasattr(status.error, "json")
                else status.error.json
            )
        else:
            body["result"] = status.result

        message = self.message_hub.create_response_or_notification(body)
        for client_id in status.clients_to_notify:
            self.message_hub.enqueue_message(message, to=client_id)

    def _on_command_execution_timeout(self, sender, statuses):
        """Handler called when the execution of a remote asynchronous
        command was abandoned with a timeout. Dispatches an appropriate
        ``ASYNC-TIMEOUT`` message.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
            statuses (List[CommandExecutionStatus]): the status objects
                corresponding to the commands whose execution has timed out.
        """
        # Multiple commands may have timed out at the same time, and we
        # need to sort them by the clients that originated these requests
        # so we can dispatch individual ASYNC-TIMEOUT messages to each of
        # them
        receipt_ids_by_clients = defaultdict(list)
        for status in statuses:
            receipt_id = status.id
            for client in status.clients_to_notify:
                receipt_ids_by_clients[client].append(receipt_id)

        hub = self.message_hub
        for client, receipt_ids in receipt_ids_by_clients.items():
            body = {"type": "ASYNC-TIMEOUT", "ids": receipt_ids}
            message = hub.create_response_or_notification(body)
            hub.enqueue_message(message, to=client)

    def _on_connection_added(
        self, sender: ConnectionRegistry, entry: ConnectionRegistryEntry
    ) -> None:
        """Handler called when a connection is added to the connection registry.

        Sends a CONN-INF notification to all connected clients so they know that
        the connection was added.

        Parameters:
            sender: the connection registry
            object: the connection that was added
        """
        notification = self.create_CONN_INF_message_for([entry.id])
        self.message_hub.enqueue_message(notification)

    def _on_connection_removed(
        self, sender: ConnectionRegistry, entry: ConnectionRegistryEntry
    ) -> None:
        """Handler called when a connection is removed from the connection
        registry.

        Sends a CONN-DEL notification to all connected clients so they know that
        the connection was removed.

        Parameters:
            sender: the connection registry
            object: the connection that was removed
        """
        notification = self.message_hub.create_response_or_notification(
            {"type": "CONN-DEL", "ids": [entry.id]}
        )
        try:
            self.message_hub.enqueue_message(notification)
        except BrokenResourceError:
            # App is probably shutting down, this is OK.
            pass

    def _on_object_removed(self, sender: ObjectRegistry, object: ModelObject) -> None:
        """Handler called when an object is removed from the object registry.

        Parameters:
            sender: the object registry
            object: the object that was removed
        """
        notification = self.message_hub.create_response_or_notification(
            {"type": "OBJ-DEL", "ids": [object.id]}
        )
        try:
            self.message_hub.enqueue_message(notification)
        except BrokenResourceError:
            # App is probably shutting down, this is OK.
            pass


############################################################################

app = SkybrushServer()

# ######################################################################## #


@app.message_hub.on("CONN-INF")
def handle_CONN_INF(message, sender, hub):
    return app.create_CONN_INF_message_for(message.body["ids"], in_response_to=message)


@app.message_hub.on("CONN-LIST")
def handle_CONN_LIST(message, sender, hub):
    return {"ids": list(app.connection_registry.ids)}


@app.message_hub.on("DEV-INF")
def handle_DEV_INF(message, sender, hub):
    return app.create_DEV_INF_message_for(message.body["paths"], in_response_to=message)


@app.message_hub.on("DEV-LIST")
def handle_DEV_LIST(message, sender, hub):
    return app.create_DEV_LIST_message_for(message.body["ids"], in_response_to=message)


@app.message_hub.on("DEV-LISTSUB")
def handle_DEV_LISTSUB(message, sender, hub):
    return app.create_DEV_LISTSUB_message_for(
        client=sender,
        path_filter=message.body.get("pathFilter", ("/",)),
        in_response_to=message,
    )


@app.message_hub.on("DEV-SUB")
def handle_DEV_SUB(message, sender, hub):
    return app.create_DEV_SUB_message_for(
        client=sender, paths=message.body["paths"], in_response_to=message
    )


@app.message_hub.on("DEV-UNSUB")
def handle_DEV_UNSUB(message, sender, hub):
    return app.create_DEV_UNSUB_message_for(
        client=sender,
        paths=message.body["paths"],
        in_response_to=message,
        remove_all=message.body.get("removeAll", False),
        include_subtrees=message.body.get("includeSubtrees", False),
    )


@app.message_hub.on("OBJ-LIST")
def handle_OBJ_LIST(message, sender, hub):
    filter = message.body.get("filter")
    if filter is None:
        it = app.object_registry.ids
    else:
        it = app.object_registry.ids_by_types(filter)
    return {"ids": list(it)}


@app.message_hub.on("SYS-PING")
def handle_SYS_PING(message, sender, hub):
    return hub.acknowledge(message)


@app.message_hub.on("SYS-TIME")
def handle_SYS_TIME(message, sender, hub):
    return {"timestamp": int(round(time() * 1000))}


@app.message_hub.on("SYS-VER")
def handle_SYS_VER(message, sender, hub):
    return {"software": "skybrushd", "version": server_version}


@app.message_hub.on("UAV-INF")
def handle_UAV_INF(message, sender, hub):
    return app.create_UAV_INF_message_for(message.body["ids"], in_response_to=message)


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message, sender, hub):
    return {"ids": list(app.object_registry.ids_by_type(UAV))}


@app.message_hub.on(
    "OBJ-CMD",
    "UAV-FLY",
    "UAV-HALT",
    "UAV-LAND",
    "UAV-MOTOR",
    "UAV-RST",
    "UAV-RTH",
    "UAV-SIGNAL",
    "UAV-TAKEOFF",
    "UAV-VER",
)
async def handle_UAV_operations(message, sender, hub):
    return await app.dispatch_to_uavs(message, sender)


# ######################################################################## #
