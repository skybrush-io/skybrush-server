"""Application and authentication objects for the Flockwave server."""

from __future__ import absolute_import

import errno
import os

from blinker import Signal
from collections import defaultdict
from future.utils import iteritems
from importlib import import_module

from flockwave.gps.vectors import GPSCoordinate

from .commands import CommandExecutionManager, CommandExecutionStatus
from .errors import NotSupportedError
from .ext.manager import ExtensionManager
from .logger import log
from .message_hub import MessageHub
from .model.devices import DeviceTree, DeviceTreeSubscriptionManager
from .model.errors import ClientNotSubscribedError, NoSuchPathError
from .model.rate_limiters import rate_limited_by, \
    UAVSpecializedMessageRateLimiter
from .model.world import World
from .registries import ChannelTypeRegistry, ClientRegistry, \
    ConnectionRegistry, UAVRegistry, find_in_registry
from .version import __version__ as server_version

__all__ = ("app", )

PACKAGE_NAME = __name__.rpartition(".")[0]


class FlockwaveServer(object):
    """Main application object for the Flockwave server.

    Attributes:
        channel_type_registry (ChannelTypeRegistry): central registry for
            types of communication channels that the server can handle and
            manage. Types of communication channels include Socket.IO
            streams, TCP or UDP sockets and so on.
        client_registry (ClientRegistry): central registry for the clients
            that are currently connected to the server
        client_count_changed (Signal): signal that is emitted when the
            number of clients connected to the server changes
        command_execution_manager (CommandExecutionManager): object that
            manages the asynchronous execution of commands on remote UAVs
            (i.e. commands that cannot be executed immediately in a
            synchronous manner)
        config (dict): dictionary holding the configuration options of the
            application
        device_tree (DeviceTree): a tree-like data structure that contains
            a first-level node for every UAV and then contains additional
            nodes in each UAV subtree for the devices and channels of the
            UAV
        extension_manager (ExtensionManager): object that manages the
            loading and unloading of server extensions
        message_hub (MessageHub): central messaging hub via which one can
            send Flockwave messages
        uav_registry (UAVRegistry): central registry for the UAVs known to
            the server
        world (World): a representation of the "world" in which the flock
            of UAVs live. By default, the world is empty but extensions may
            extend it with objects.
    """

    num_clients_changed = Signal()

    def __init__(self):
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
        # Create the configuration dictionary
        self.config = {}

        # Create an object to hold information about all the registered
        # communication channel types that the server can handle
        self.channel_type_registry = ChannelTypeRegistry()

        # Create an object to hold information about all the connected
        # clients that the server can talk to
        self.client_registry = ClientRegistry()
        self.client_registry.channel_type_registry = \
            self.channel_type_registry
        self.client_registry.count_changed.connect(
            self._on_client_count_changed,
            sender=self.client_registry
        )

        # Create an object that keeps track of commands being executed
        # asynchronously on remote UAVs
        self.command_execution_manager = CommandExecutionManager()
        self.command_execution_manager.expired.connect(
            self._on_command_execution_timeout,
            sender=self.command_execution_manager
        )
        self.command_execution_manager.finished.connect(
            self._on_command_execution_finished,
            sender=self.command_execution_manager
        )

        # Creates an object to hold information about all the connections
        # to external data sources that the server manages
        self.connection_registry = ConnectionRegistry()
        self.connection_registry.connection_state_changed.connect(
            self._on_connection_state_changed,
            sender=self.connection_registry
        )

        # Create a message hub that will handle incoming and outgoing
        # messages
        self.message_hub = MessageHub()
        self.message_hub.channel_type_registry = self.channel_type_registry
        self.message_hub.client_registry = self.client_registry

        # Create an object to hold information about all the UAVs that
        # the server knows about
        self.uav_registry = UAVRegistry()

        # Create the global world object
        self.world = World()

        # Create a global device tree and ensure that new UAVs are
        # registered in it
        self.device_tree = DeviceTree()
        self.device_tree.uav_registry = self.uav_registry

        # Create an object to manage the associations between clients and
        # the device tree paths that the clients are subscribed to
        self.device_tree_subscriptions = DeviceTreeSubscriptionManager(
            self.device_tree)
        self.device_tree_subscriptions.client_registry = self.client_registry
        self.device_tree_subscriptions.message_hub = self.message_hub

    def create_CMD_DEL_message_for(self, receipt_ids, in_response_to):
        """Creates a CMD-DEL message after having cancelled the execution of
        the asynchronous commands with the given receipt IDs.

        Parameters:
            receipt_ids (iterable): list of receipt IDs
            in_response_to (FlockwaveMessage or None): the message that the
                constructed message will respond to.

        Returns:
            FlockwaveMessage: the CMD-DEL message that acknowledges the
                cancellation of the given asynchronous commands
        """
        body = {"type": "CMD-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

        for receipt_id in receipt_ids:
            entry = self._find_command_receipt_by_id(receipt_id, response)
            if entry:
                self.command_execution_manager.cancel(entry)
                response.add_success(receipt_id)

        return response

    def create_CMD_INF_message_for(self, receipt_ids,
                                   in_response_to=None):
        """Creates a CMD-INF message that contains information regarding
        the asynchronous commands being executed with the given receipt IDs.

        Parameters:
            receipt_ids (iterable): list of receipt IDs
            in_response_to (FlockwaveMessage or None): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the CMD-INF message with the status info of
                the given asynchronous commands
        """
        receipts = {}

        body = {"receipts": receipts, "type": "CMD-INF"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

        for receipt_id in receipt_ids:
            entry = self._find_command_receipt_by_id(receipt_id, response)
            if entry:
                receipts[receipt_id] = entry.json

        return response

    def create_CONN_INF_message_for(self, connection_ids,
                                    in_response_to=None):
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
            body=body, in_response_to=in_response_to)

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

    def create_DEV_LIST_message_for(self, uav_ids, in_response_to=None):
        """Creates a DEV-LIST message that contains information regarding
        the device trees of the UAVs with the given IDs.

        Parameters:
            uav_ids (iterable): list of UAV IDs
            in_response_to (Optional[FlockwaveMessage]): the message that the
                constructed message will respond to. ``None`` means that the
                constructed message will be a notification.

        Returns:
            FlockwaveMessage: the DEV-LIST message with the device trees of
                the given UAVs
        """
        devices = {}

        body = {"devices": devices, "type": "DEV-LIST"}
        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

        for uav_id in uav_ids:
            uav = self._find_uav_by_id(uav_id, response)
            if uav:
                devices[uav_id] = uav.device_tree_node.json

        return response

    def create_DEV_LISTSUB_message_for(self, client, path_filter,
                                       in_response_to=None):
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

        body = {
            "paths": list(subscriptions.elements()),
            "type": "DEV-LISTSUB"
        }

        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to)

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
            {}, in_response_to=in_response_to)

        for path in paths:
            try:
                manager.subscribe(client, path)
            except NoSuchPathError:
                response.add_failure(path, "No such device tree path")
            else:
                response.add_success(path)

        return response

    def create_DEV_UNSUB_message_for(self, client, paths, in_response_to,
                                     remove_all, include_subtrees):
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
            {}, in_response_to=in_response_to)

        if include_subtrees:
            # Collect all the subscriptions from the subtrees and pretend
            # that the user submitted that
            paths = manager.list_subscriptions(client, paths)

        for path in paths:
            try:
                manager.unsubscribe(client, path, force=remove_all)
            except NoSuchPathError:
                response.add_failure(path, "No such device tree path")
            except ClientNotSubscribedError:
                response.add_failure(path, "Not subscribed to this path")
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
            body=body, in_response_to=in_response_to)

        for uav_id in uav_ids:
            uav = self._find_uav_by_id(uav_id, response)
            if uav:
                statuses[uav_id] = uav.status.json

        return response

    def dispatch_to_uavs(self, message, sender):
        """Dispatches a message intended for multiple UAVs to the appropriate
        UAV drivers.

        Parameters:
            message (FlockwaveMessage): the message that contains a request
                that is to be forwarded to multiple UAVs. The message is
                expected to have an ``ids`` property that lists the UAVs
                to dispatch the message to.
            sender (Client): the client that sent the message

        Returns:
            FlockwaveMessage: a response to the original message that lists
                the IDs of the UAVs for which the message has been sent
                successfully and also the IDs of the UAVs for which the
                dispatch failed (in the ``success`` and ``failure`` keys).
        """
        cmd_manager = self.command_execution_manager

        # Create the response
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=message)

        # Process the body
        parameters = dict(message.body)
        message_type = parameters.pop("type")
        uav_ids = parameters.pop("ids")

        # Sort the UAVs being targeted by drivers
        uavs_by_drivers = self._sort_uavs_by_drivers(uav_ids, response)

        # Find the method to invoke on the driver
        method_name, transformer = {
            "CMD-REQ": ("send_command", None),
            "UAV-FLY": ("send_fly_to_target_signal", {
                "target": GPSCoordinate.from_json
            }),
            "UAV-HALT": ("send_shutdown_signal", None),
            "UAV-LAND": ("send_landing_signal", None),
            "UAV-RTH": ("send_return_to_home_signal", None),
            "UAV-TAKEOFF": ("send_takeoff_signal", None)
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
        for driver, uavs in iteritems(uavs_by_drivers):
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
                except RuntimeError as ex:
                    common_error = "Unexpected error: {0}".format(ex)
                    log.exception(ex)

            # Update the response
            if common_error is not None:
                for uav in uavs:
                    response.add_failure(uav.id, common_error)
            else:
                for uav, result in iteritems(results):
                    if result is True:
                        response.add_success(uav.id)
                    elif isinstance(result, CommandExecutionStatus):
                        response.add_receipt(uav.id, result)
                        result.add_client_to_notify(sender.id)
                        cmd_manager.mark_as_clients_notified(result.id)
                    else:
                        response.add_failure(uav.id, result)

        return response

    def import_api(self, extension_name):
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
        return self.extension_manager.import_api(extension_name)

    @property
    def num_clients(self):
        """The number of clients connected to the server."""
        return self.client_registry.num_entries

    def prepare(self, config):
        """Hook function that contains preparation steps that should be
        performed by the server before it starts serving requests.

        Parameters:
            config (Optional[str]): name of the configuration file to load

        Returns:
            Optional[int]: error code to terminate the app with if the
                preparation was not successful; ``None`` if the preparation
                was successful
        """
        # Load the configuration
        if not self._load_configuration(config):
            return 1

        # Process the configuration options
        cfg = self.config.get("COMMAND_EXECUTION_MANAGER", {})
        self.command_execution_manager.timeout = cfg.get("timeout", 30)

        # Import and configure the extensions that we want to use. This
        # must be done last because we want to be sure that the basic
        # components of the app (prepared above) are ready.
        self.extension_manager = ExtensionManager(self)
        self.extension_manager.configure(self.config.get("EXTENSIONS", {}))

    @rate_limited_by(UAVSpecializedMessageRateLimiter, timeout=0.1)
    def request_to_send_UAV_INF_message_for(self, uav_ids):
        """Requests the application to send an UAV-INF message that contains
        information regarding the UAVs with the given IDs. The application
        may send the message immediately or opt to delay it a bit in order
        to ensure that UAV-INF notifications are not emitted too frequently.

        Parameters:
            uav_ids (iterable): list of UAV IDs
        """
        message = self.create_UAV_INF_message_for(uav_ids)
        self.message_hub.send_message(message)

    def teardown(self):
        """Tears down the application and prepares it for exiting normally."""
        self.extension_manager.teardown()

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
        return find_in_registry(self.command_execution_manager,
                                receipt_id, response, "No such receipt")

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
        return find_in_registry(self.connection_registry,
                                connection_id, response, "No such connection")

    def _find_uav_by_id(self, uav_id, response=None):
        """Finds the UAV with the given ID in the UAV registry or registers
        a failure in the given response object if there is no UAV with the
        given ID.

        Parameters:
            uav_id (str): the ID of the UAV to find
            response (Optional[FlockwaveResponse]): the response in which
                the failure can be registered

        Returns:
            Optional[UAV]: the UAV with the given ID or ``None`` if there
                is no such UAV
        """
        return find_in_registry(self.uav_registry, uav_id, response,
                                "No such UAV")

    def _load_base_configuration(self):
        """Loads the default configuration of the application from the
        `flockwave.server.config` module.
        """
        config = import_module(".config", PACKAGE_NAME)
        self._load_configuration_from_object(config)

    def _load_configuration(self, config=None):
        """Loads the configuration of the application from the following
        sources, in the following order:

        - The default configuration in the `flockwave.server.config`
          module.

        - The configuration file referred to by the `config` argument,
          if present.

        - The configuration file referred to by the `FLOCKWAVE_SETTINGS`
          environment variable, if it is specified.

        Parameters:
            config (Optional[str]): name of the configuration file to load

        Returns:
            bool: whether all configuration files were processed successfully
        """
        self._load_base_configuration()

        config_files = [
            (config, True),
            (os.environ.get("FLOCKWAVE_SETTINGS"), True)
        ]

        if not config:
            config_files.insert(0, ("flockwave.cfg", False))

        return all(
            self._load_configuration_from_file(config_file, mandatory)
            for config_file, mandatory in config_files if config_file
        )

    def _load_configuration_from_file(self, filename, mandatory=True):
        """Loads configuration settings from the given file.

        Parameters:
            filename (str): name of the configuration file to load. Relative
                paths are resolved from the current directory.
            mandatory (bool): whether the configuration file must exist.
                If this is ``False`` and the file does not exist, this
                function will not print a warning about the missing file
                and pretend that loading the file was successful.

        Returns:
            bool: whether the configuration was loaded successfully
        """
        original, filename = filename, os.path.abspath(filename)

        exists = True
        try:
            config = {}
            with open(filename, mode='rb') as config_file:
                exec(compile(config_file.read(), filename, 'exec'), config)
        except IOError as e:
            if e.errno in (errno.ENOENT, errno.EISDIR, errno.ENOTDIR):
                exists = False
            else:
                raise

        self._load_configuration_from_object(config)

        if not exists and mandatory:
            log.warn("Cannot load configuration from {0!r}".format(original))
            return False
        elif exists:
            log.info("Loaded configuration from {0!r}".format(original))

        return True

    def _load_configuration_from_object(self, config):
        """Loads configuration settings from the given Python object.

        Only uppercase keys will be processed.

        Parameters:
            config (object): the configuration object to load.
        """
        for key in dir(config):
            if key.isupper():
                self.config[key] = getattr(config, key)

    def _on_client_count_changed(self, sender):
        """Handler called when the number of clients attached to the server
        has changed. Dispatches a ``num_clients_changed`` signal.
        """
        self.num_clients_changed.send(self)

    def _on_connection_state_changed(self, sender, entry, old_state,
                                     new_state):
        """Handler called when the state of a connection changes somewhere
        within the server. Dispatches an appropriate ``CONN-INF`` message.

        Parameters:
            sender (ConnectionRegistry): the connection registry
            entry (ConnectionEntry): a connection entry from the connection
                registry
            old_state (ConnectionState): the old state of the connection
            new_state (ConnectionState): the old state of the connection
        """
        message = self.create_CONN_INF_message_for([entry.id])
        self.message_hub.send_message(message)

    def _on_command_execution_finished(self, sender, status):
        """Handler called when the execution of a remote asynchronous
        command finished. Dispatches an appropriate ``CMD-RESP`` message.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
            status (CommandExecutionStatus): the status object corresponding
                to the command whose execution has just finished.
        """
        body = {
            "type": "CMD-RESP",
            "id": status.id,
            "response": status.response if status.response is not None else ""
        }
        message = self.message_hub.create_response_or_notification(body)
        for client_id in status.clients_to_notify:
            self.message_hub.send_message(message, to=client_id)

    def _on_command_execution_timeout(self, sender, statuses):
        """Handler called when the execution of a remote asynchronous
        command was abandoned with a timeout. Dispatches an appropriate
        ``CMD-TIMEOUT`` message.

        Parameters:
            sender (CommandExecutionManager): the command execution manager
            statuses (List[CommandExecutionStatus]): the status objects
                corresponding to the commands whose execution has timed out.
        """
        # Multiple commands may have timed out at the same time, and we
        # need to sort them by the clients that originated these requests
        # so we can dispatch individual CMD-TIMEOUT messages to each of
        # them
        receipt_ids_by_clients = defaultdict(list)
        for status in statuses:
            receipt_id = status.id
            for client in status.clients_to_notify:
                receipt_ids_by_clients[client].append(receipt_id)

        hub = self.message_hub
        for client, receipt_ids in iteritems(receipt_ids_by_clients):
            body = {
                "type": "CMD-TIMEOUT",
                "ids": receipt_ids
            }
            message = hub.create_response_or_notification(body)
            hub.send_message(message, to=client)

    def _sort_uavs_by_drivers(self, uav_ids, response=None):
        """Given a list of UAV IDs, returns a mapping that maps UAV drivers
        to the UAVs specified by the IDs.

        Parameters:
            uav_ids (List[str]): list of UAV IDs
            response (Optional[FlockwaveResponse]): optional response in
                which UAV lookup failures can be registered

        Returns:
            Dict[UAVDriver,UAV]: mapping of UAV drivers to the UAVs that
                were selected by the given UAV IDs
        """
        result = defaultdict(list)
        for uav_id in uav_ids:
            uav = self._find_uav_by_id(uav_id, response)
            if uav:
                result[uav.driver].append(uav)
        return result


############################################################################

app = FlockwaveServer()

# ######################################################################## #


@app.message_hub.on("CMD-DEL")
def handle_CMD_DEL(message, sender, hub):
    return app.create_CMD_DEL_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("CMD-INF")
def handle_CMD_INF(message, sender, hub):
    return app.create_CMD_INF_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("CONN-INF")
def handle_CONN_INF(message, sender, hub):
    return app.create_CONN_INF_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("CONN-LIST")
def handle_CONN_LIST(message, sender, hub):
    return {
        "ids": list(app.connection_registry.ids)
    }


@app.message_hub.on("DEV-INF")
def handle_DEV_INF(message, sender, hub):
    return app.create_DEV_INF_message_for(
        message.body["paths"], in_response_to=message
    )


@app.message_hub.on("DEV-LIST")
def handle_DEV_LIST(message, sender, hub):
    return app.create_DEV_LIST_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("DEV-LISTSUB")
def handle_DEV_LISTSUB(message, sender, hub):
    return app.create_DEV_LISTSUB_message_for(
        client=sender,
        path_filter=message.body.get("pathFilter", ("/", )),
        in_response_to=message
    )


@app.message_hub.on("DEV-SUB")
def handle_DEV_SUB(message, sender, hub):
    return app.create_DEV_SUB_message_for(
        client=sender,
        paths=message.body["paths"],
        in_response_to=message
    )


@app.message_hub.on("DEV-UNSUB")
def handle_DEV_UNSUB(message, sender, hub):
    return app.create_DEV_UNSUB_message_for(
        client=sender,
        paths=message.body["paths"],
        in_response_to=message,
        remove_all=message.body.get("removeAll", False),
        include_subtrees=message.body.get("includeSubtrees", False)
    )


@app.message_hub.on("SYS-PING")
def handle_SYS_PING(message, sender, hub):
    return hub.acknowledge(message)


@app.message_hub.on("SYS-VER")
def handle_SYS_VER(message, sender, hub):
    return {
        "software": "flockwave-server",
        "version": server_version
    }


@app.message_hub.on("UAV-INF")
def handle_UAV_INF(message, sender, hub):
    return app.create_UAV_INF_message_for(
        message.body["ids"], in_response_to=message
    )


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message, sender, hub):
    return {
        "ids": list(app.uav_registry.ids)
    }


@app.message_hub.on("CMD-REQ", "UAV-FLY", "UAV-HALT", "UAV-LAND", "UAV-RTH",
                    "UAV-TAKEOFF")
def handle_UAV_operations(message, sender, hub):
    return app.dispatch_to_uavs(message, sender)


# ######################################################################## #
