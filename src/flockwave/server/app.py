"""Application object for the Skybrush server."""

from collections import Counter, defaultdict
from inspect import isawaitable, isasyncgen
from os import environ
from platformdirs import AppDirs
from trio import (
    BrokenResourceError,
    move_on_after,
)
from typing import (
    Any,
    Iterable,
    Optional,
    Sequence,
    Union,
)

from flockwave.app_framework import DaemonApp
from flockwave.app_framework.configurator import AppConfigurator, Configuration
from flockwave.connections.base import ConnectionState
from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.ports import get_port_map, set_base_port
from flockwave.server.utils import divide_by, rename_keys
from flockwave.server.utils.packaging import is_packaged
from flockwave.server.utils.system_time import (
    can_set_system_time_detailed_async,
    get_system_time_msec,
    set_system_time_msec_async,
)

from .commands import CommandExecutionManager, CommandExecutionStatus
from .errors import NotSupportedError
from .logger import log
from .message_hub import (
    BatchMessageRateLimiter,
    ConnectionStatusMessageRateLimiter,
    MessageHub,
    RateLimiters,
    UAVMessageRateLimiter,
)
from .message_handlers import MessageBodyTransformationSpec, transform_message_body
from .model.client import Client
from .model.devices import DeviceTree, DeviceTreeSubscriptionManager
from .model.errors import ClientNotSubscribedError, NoSuchPathError
from .model.log import LogMessage, Severity
from .model.messages import FlockwaveMessage, FlockwaveNotification, FlockwaveResponse
from .model.object import ModelObject
from .model.transport import TransportOptions
from .model.uav import is_uav, UAV, UAVDriver
from .model.world import World
from .registries import (
    ChannelTypeRegistry,
    ClientRegistry,
    ConnectionRegistry,
    ConnectionRegistryEntry,
    ObjectRegistry,
    UAVDriverRegistry,
    find_in_registry,
)
from .version import __version__ as server_version

__all__ = ("app",)

PACKAGE_NAME = __name__.rpartition(".")[0]


#: Table that describes the handlers of several UAV-related command requests
UAV_COMMAND_HANDLERS: dict[str, tuple[str, MessageBodyTransformationSpec]] = {
    "LOG-DATA": ("get_log", rename_keys({"logId": "log_id"})),
    "LOG-INF": ("get_log_list", None),
    "OBJ-CMD": ("send_command", None),
    "PRM-GET": ("get_parameter", None),
    "PRM-SET": ("set_parameter", None),
    "PRM-SET-MANY": ("set_parameters", None),
    "UAV-CALIB": ("calibrate_component", None),
    "UAV-FLY": (
        "send_fly_to_target_signal",
        {"target": GPSCoordinate.from_json},
    ),
    "UAV-HALT": ("send_shutdown_signal", {"transport": TransportOptions.from_json}),
    "UAV-HOVER": ("send_hover_signal", {"transport": TransportOptions.from_json}),
    "UAV-LAND": ("send_landing_signal", {"transport": TransportOptions.from_json}),
    "UAV-MOTOR": (
        "send_motor_start_stop_signal",
        {"transport": TransportOptions.from_json},
    ),
    "UAV-PREFLT": ("request_preflight_report", None),
    "UAV-RST": ("send_reset_signal", {"transport": TransportOptions.from_json}),
    "UAV-RTH": (
        "send_return_to_home_signal",
        {"transport": TransportOptions.from_json},
    ),
    "UAV-SIGNAL": (
        "send_light_or_sound_emission_signal",
        {"duration": divide_by(1000), "transport": TransportOptions.from_json},
    ),
    "UAV-SLEEP": (
        "enter_low_power_mode",
        {"transport": TransportOptions.from_json},
    ),
    "UAV-TAKEOFF": ("send_takeoff_signal", {"transport": TransportOptions.from_json}),
    "UAV-TEST": ("test_component", None),
    "UAV-VER": ("request_version_info", None),
    "UAV-WAKEUP": (
        "resume_from_low_power_mode",
        {"transport": TransportOptions.from_json},
    ),
}

#: Constant for a dummy UAV command handler that does nothing
NULL_HANDLER = (None, None)


class SkybrushServer(DaemonApp):
    """Main application object for the Skybrush server."""

    channel_type_registry: ChannelTypeRegistry
    """Central registry for types of communication channels that the server can
    handle and manage. Types of communication channels include Socket.IO
    streams, TCP or UDP sockets and so on.
    """

    client_registry: ClientRegistry
    """Registry for the clients that are currently connected to the server."""

    command_execution_manager: CommandExecutionManager
    """Object that manages the asynchronous execution of commands on remote UAVs
    (i.e. commands that cannot be executed immediately in a synchronous manner)
    """

    device_tree: DeviceTree
    """Tree-like data structure that contains a first-level node for every UAV
    and then contains additional nodes in each UAV subtree for the devices and
    channels of the UAV.
    """

    message_hub: MessageHub
    """Central messaging hub via which one can send Flockwave messages."""

    object_registry: ObjectRegistry
    """Central registry for the objects known to the server."""

    uav_driver_registry: UAVDriverRegistry
    """Registry for UAV drivers that are currently registered in the server."""

    world: World
    """A representation of the "world" in which the flock of UAVs live. By
    default, the world is empty but extensions may extend it with objects.
    """

    _registry_full_error_counts: Counter[Any]
    """Object that counts how many times we did report a registry full error
    for a given source. This is used to limit the number of warnings we print.
    """

    def cancel_async_operations(
        self, receipt_ids: Iterable[str], in_response_to: FlockwaveMessage
    ) -> FlockwaveResponse:
        """Handles a request to cancel one or more pending asynchronous operations,
        identified by their receipt IDs.

        Parameters:
            receipt_ids: the receipt IDs of the pending asynchronous operations
            in_response_to: the message that the constructed message will
                respond to
        """
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=in_response_to
        )
        valid_ids: list[str] = []

        manager = self.command_execution_manager

        for receipt_id in receipt_ids:
            if manager.is_valid_receipt_id(receipt_id):
                valid_ids.append(receipt_id)
                response.add_success(receipt_id)
            else:
                response.add_error(receipt_id, "no such receipt")

        for receipt_id in valid_ids:
            manager.cancel(receipt_id)

        return response

    def create_CONN_INF_message_for(
        self,
        connection_ids: Iterable[str],
        in_response_to: Optional[FlockwaveMessage] = None,
    ) -> FlockwaveMessage:
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

    def create_DEV_INF_message_for(
        self, paths: Iterable[str], in_response_to: Optional[FlockwaveMessage] = None
    ) -> FlockwaveMessage:
        """Creates a DEV-INF message that contains information regarding
        the current values of the channels in the subtrees of the device
        tree matched by the given device tree paths.

        Parameters:
            paths: list of device tree paths
            in_response_to: the message that the constructed message will
                respond to. ``None`` means that the constructed message will be
                a notification.

        Returns:
            the DEV-INF message with the current values of the channels in the
            subtrees matched by the given device tree paths
        """
        return self.device_tree_subscriptions.create_DEV_INF_message_for(
            paths, in_response_to
        )

    def create_DEV_LIST_message_for(
        self,
        object_ids: Iterable[str],
        in_response_to: FlockwaveMessage,
    ) -> FlockwaveMessage:
        """Creates a DEV-LIST message that contains information regarding
        the device trees of the objects with the given IDs.

        Parameters:
            object_ids: list of object IDs
            in_response_to: the message that the constructed message will
                respond to.

        Returns:
            the DEV-LIST message with the device trees of the given objects
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
                    devices[object_id] = object.device_tree_node.json  # type: ignore
                else:
                    devices[object_id] = {}

        return response

    def create_DEV_LISTSUB_message_for(
        self,
        client: Client,
        path_filter: Iterable[str],
        in_response_to: FlockwaveMessage,
    ):
        """Creates a DEV-LISTSUB message that contains information about the
        device tree paths that the given client is subscribed to.

        Parameters:
            client: the client whose subscriptions we are interested in
            path_filter: list of device tree paths whose subtrees
                the client is interested in
            in_response_to: the message that the constructed message will
                respond to. ``None`` means that the constructed message will be
                a notification.

        Returns:
            the DEV-LISTSUB message with the subscriptions of the client that
            match the path filters
        """
        manager = self.device_tree_subscriptions
        subscriptions = manager.list_subscriptions(client, path_filter)

        body = {"paths": list(subscriptions.elements()), "type": "DEV-LISTSUB"}

        response = self.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        return response

    def create_DEV_SUB_message_for(
        self,
        client: Client,
        paths: Iterable[str],
        lazy: bool,
        in_response_to: FlockwaveMessage,
    ) -> FlockwaveMessage:
        """Creates a DEV-SUB response for the given message and subscribes
        the given client to the given paths.

        Parameters:
            client: the client to subscribe to the given paths
            paths: list of device tree paths to subscribe the client to
            lazy: whether the client is allowed to subscribe to paths that do
                not exist yet.
            in_response_to: the message that the constructed message will
                respond to.

        Returns:
            the DEV-SUB message with the paths that the client was subscribed
            to, along with error messages for the paths that the client was not
            subscribed to
        """
        manager = self.device_tree_subscriptions
        response = self.message_hub.create_response_or_notification(
            {}, in_response_to=in_response_to
        )

        for path in paths:
            try:
                manager.subscribe(client, path, lazy)
            except NoSuchPathError:
                response.add_error(path, "No such device tree path")
            else:
                response.add_success(path)

        return response

    def create_DEV_UNSUB_message_for(
        self,
        client: Client,
        paths: Iterable[str],
        *,
        in_response_to: FlockwaveMessage,
        remove_all: bool,
        include_subtrees: bool,
    ) -> FlockwaveResponse:
        """Creates a DEV-UNSUB response for the given message and
        unsubscribes the given client from the given paths.

        Parameters:
            client: the client to unsubscribe from the given paths
            paths: list of device tree paths to unsubscribe the
                given client from
            in_response_to: the message that the
                constructed message will respond to.
            remove_all: when ``True``, the client will be unsubscribed
                from the given paths no matter how many times it is
                subscribed to them. When ``False``, an unsubscription will
                decrease the number of subscriptions to the given path by
                1 only.
            include_subtrees: when ``True``, subscriptions to nodes
                that are in the subtrees of the given paths will also be
                removed

        Returns:
            the DEV-UNSUB message with the paths that the client was
            unsubscribed from, along with error messages for the paths that the
            client was not unsubscribed from
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

    def create_SYS_MSG_message_from(
        self, messages: Iterable[LogMessage]
    ) -> FlockwaveNotification:
        """Creates a SYS-MSG message containing the given list of log messages.

        Typically, you should not use this method (unless you know what you are
        doing) because allows one to bypass the built-in rate limiting for
        SYS-MSG messages. If you only want to broadcast SYS-MSG messages to all
        interested parties, use ``request_to_send_SYS_MSG_message()``
        instead, which will send the notification immediately if the rate
        limiting constraints allow, but it may also wait a bit if the
        SYS-MSG messages are sent too frequently.

        Parameters:
            messages: iterable of log messages to put in the generated SYS-MSG
                message

        Returns:
            FlockwaveNotification: the SYS-MSG message with the given log
                messages
        """
        body = {"items": list(messages), "type": "SYS-MSG"}
        return self.message_hub.create_response_or_notification(body=body)

    def create_UAV_INF_message_for(
        self, uav_ids: Iterable[str], in_response_to: Optional[FlockwaveMessage] = None
    ):
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
            uav_ids: list of UAV IDs
            in_response_to: the message that the constructed message will
                respond to. ``None`` means that the constructed message will be
                a notification.

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
                statuses[uav_id] = uav.status.json  # type: ignore

        return response

    async def disconnect_client(
        self, client: Client, reason: Optional[str] = None, timeout: float = 10
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

    async def dispatch_to_uav(
        self, message: FlockwaveMessage, sender: Client, *, id_property: str = "id"
    ) -> FlockwaveMessage:
        """Dispatches a message intended for a single UAV to the appropriate
        UAV driver.

        Parameters:
            message: the message that contains a request that is to be forwarded
                to a single UAV. The message is expected to have an ``id``
                property that contains the ID of the UAV to dispatch the message
                to. The name of the property can be overridden with the
                ``id_property`` parameter.
            sender: the client that sent the message
            id_property: name of the property in the message that contains the
                ID of the UAV to dispatch the message to

        Returns:
            a response to the original message that contains exactly one of the
            following three keys: ``result`` for the result of a successful
            message dispatch, ``error`` for a message dispatch that threw an
            error, or ``receipt`` if calling the message handler returned an
            awaitable
        """
        # Create the response
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=message
        )

        # Process the body
        parameters = dict(message.body)
        message_type = parameters.pop("type")
        uav_id: Optional[str] = parameters.pop(id_property, None)
        uav: Optional[UAV] = None
        error: Optional[str] = None
        result: Any = None

        try:
            if uav_id is None:
                raise RuntimeError("message must contain a UAV ID")

            # Find the driver of the UAV
            uav = self.find_uav_by_id(uav_id)
            if uav is None:
                raise RuntimeError("no such UAV")

            # Find the method to invoke on the driver
            method_name, transformer = UAV_COMMAND_HANDLERS.get(
                message_type, NULL_HANDLER
            )

            # Transform the incoming arguments if needed before sending them
            # to the driver method
            parameters = transform_message_body(transformer, parameters)

            # Look up the method in the driver
            try:
                method = getattr(uav.driver, method_name)  # type: ignore
            except (AttributeError, RuntimeError, TypeError):
                raise RuntimeError("Operation not supported") from None

            # Execute the method and catch all runtime errors
            result = method(uav, **parameters)
        except NotImplementedError:
            error = "Operation not implemented"
        except NotSupportedError:
            error = "Operation not supported"
        except RuntimeError as ex:
            error = str(ex)
        except Exception as ex:
            error = "Unexpected error: {0}".format(ex)
            log.exception(ex)

        # Update the response
        if error is not None:
            response.body["error"] = error
        elif isinstance(result, Exception):
            response.body["error"] = str(result)
        elif isawaitable(result) or isasyncgen(result):
            assert uav is not None
            cmd_manager = self.command_execution_manager
            receipt = cmd_manager.new(client_to_notify=sender.id)
            response.body["receipt"] = receipt.id
            response.when_sent(cmd_manager.mark_as_clients_notified, receipt.id, result)
        else:
            response.body["result"] = result

        return response

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
        # Create the response
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=message
        )

        # Process the body
        parameters = dict(message.body)
        message_type = parameters.pop("type")
        uav_ids: Sequence[str] = parameters.pop("ids", ())
        transport: Any = parameters.get("transport")

        # Sort the UAVs being targeted by drivers. If `transport` is a
        # TransportOptions object and it indicates that we should ignore the
        # UAV IDs, get hold of all registered UAV drivers as well and extend
        # the uavs_by_drivers dict
        uavs_by_drivers = self.sort_uavs_by_drivers(uav_ids, response)
        if transport and isinstance(transport, dict) and transport.get("ignoreIds"):
            # TODO(ntamas): we do not have legitimate ways to communicate an
            # error back from a driver if the driver has no associated UAVs.
            for driver in self.uav_driver_registry:
                if driver not in uavs_by_drivers:
                    uavs_by_drivers[driver] = []

        # Find the method to invoke on the driver
        method_name, transformer = UAV_COMMAND_HANDLERS.get(message_type, NULL_HANDLER)

        # Transform the incoming arguments if needed before sending them
        # to the driver method
        parameters = transform_message_body(transformer, parameters)

        # Ask each affected driver to send the message to the UAV
        for driver, uavs in uavs_by_drivers.items():
            # Look up the method in the driver
            common_error, results = None, None
            try:
                method = getattr(driver, method_name)  # type: ignore
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
                        elif isawaitable(result) or isasyncgen(result):
                            cmd_manager = self.command_execution_manager
                            receipt = cmd_manager.new(client_to_notify=sender.id)
                            response.add_receipt(uav.id, receipt)
                            response.when_sent(
                                cmd_manager.mark_as_clients_notified, receipt.id, result
                            )
                        else:
                            response.add_result(uav.id, result)

        return response

    def find_uav_by_id(
        self,
        uav_id: str,
        response: Optional[Union[FlockwaveResponse, FlockwaveNotification]] = None,
    ) -> Optional[UAV]:
        """Finds the UAV with the given ID in the object registry or registers
        a failure in the given response object if there is no UAV with the
        given ID.

        Parameters:
            uav_id: the ID of the UAV to find
            response: the response in which the failure can be registered

        Returns:
            the UAV with the given ID or ``None`` if there is no such UAV
        """
        return find_in_registry(
            self.object_registry,
            uav_id,
            predicate=is_uav,
            response=response,
            failure_reason="No such UAV",
        )  # type: ignore

    def handle_registry_full_error(self, source: Any, object: str) -> None:
        """Commomn handler for events when an extension tries to register an
        object in the object registry and the registry reaches the limits
        dictated by the license.

        Args:
            source: the source of the event. This object is simply used by
                identity to limit the number of warnings we print on the console
                for a single source.
            object: the type of the object that the caller attempted to register,
                in a human-readable form. This will be used in the warning being
                printed from this function.
        """
        cnt = self._registry_full_error_counts[source]
        if cnt <= 5:
            self._registry_full_error_counts[source] += 1
            self.log.warning(
                f"Error while registering a new {object}: server limits reached",
            )
            if cnt == 5:
                self.log.warning(
                    f"Further warnings about limits affecting a new {object} "
                    f"will be suppressed"
                )

    @property
    def num_clients(self) -> int:
        """The number of clients connected to the server."""
        return self.client_registry.num_entries

    def resume_async_operations(
        self,
        receipt_ids: Iterable[str],
        values: dict[str, Any],
        in_response_to: FlockwaveMessage,
    ) -> FlockwaveResponse:
        """Handles a request to resume one or more pending asynchronous operations,
        identified by their receipt IDs.

        Parameters:
            receipt_ids: the receipt IDs of the suspended asynchronous operations
            values: mapping from receipt IDs to the values to send back into
                the suspended asynchronous operations
            in_response_to: the message that the constructed message will
                respond to
        """
        response = self.message_hub.create_response_or_notification(
            body={}, in_response_to=in_response_to
        )
        valid_ids: list[str] = []

        if not isinstance(values, dict):
            for receipt_id in receipt_ids:
                response.add_error(receipt_id, "invalid values")
            return response

        manager = self.command_execution_manager

        for receipt_id in receipt_ids:
            if manager.is_valid_receipt_id(receipt_id):
                receipt = manager.find_by_id(receipt_id)
                if receipt.is_suspended:
                    valid_ids.append(receipt_id)
                    response.add_success(receipt_id)
                else:
                    response.add_error(receipt_id, "command is not suspended")
            else:
                response.add_error(receipt_id, "no such receipt")

        for receipt_id in valid_ids:
            manager.resume(receipt_id, values.get(receipt_id))

        return response

    def request_to_send_SYS_MSG_message(
        self,
        message: str,
        *,
        severity: Severity = Severity.INFO,
        sender: Optional[str] = None,
        timestamp: Optional[int] = None,
    ):
        """Requests the application to send a SYS-MSG message to the connected
        clients with the given message body, severity, sender ID and timestamp.
        The application may send the message immediately or opt to delay it a
        bit in order to ensure that SYS-MSG notifications are not emitted too
        frequently.

        Parameters:
            message: the body of the message
            severity: the severity level of the message
            sender: the ID of the object that the message originates from if
                the server is relaying messages from an object that it manages
                (e.g. an UAV), or `None` if the server sends the message on its
                own
            timestamp: the timestamp of the message; `None` means that the
                timestamp is not relevant and it will be omitted from the
                generated message
        """
        entry = LogMessage(
            message=message, severity=severity, sender=sender, timestamp=timestamp
        )
        self.rate_limiters.request_to_send("SYS-MSG", entry)

    def request_to_send_UAV_INF_message_for(self, uav_ids: Iterable[str]) -> None:
        """Requests the application to send an UAV-INF message that contains
        information regarding the UAVs with the given IDs. The application
        may send the message immediately or opt to delay it a bit in order
        to ensure that UAV-INF notifications are not emitted too frequently.

        Parameters:
            uav_ids: list of UAV IDs
        """
        self.rate_limiters.request_to_send("UAV-INF", uav_ids)

    async def run(self) -> int:
        self._registry_full_error_counts = Counter()

        self.run_in_background(self.command_execution_manager.run)
        self.run_in_background(self.message_hub.run)
        self.run_in_background(self.rate_limiters.run)
        return await super().run()

    def sort_uavs_by_drivers(
        self, uav_ids: Iterable[str], response: Optional[FlockwaveResponse] = None
    ) -> dict[UAVDriver, list[UAV]]:
        """Given a list of UAV IDs, returns a mapping that maps UAV drivers
        to the UAVs specified by the IDs.

        Parameters:
            uav_ids: list of UAV IDs
            response: optional response in which UAV lookup failures can be
                registered

        Returns:
            mapping of UAV drivers to the UAVs that were selected by the given UAV IDs
        """
        result: defaultdict[UAVDriver, list[UAV]] = defaultdict(list)
        for uav_id in uav_ids:
            uav = self.find_uav_by_id(uav_id, response)
            if uav:
                result[uav.driver].append(uav)
        return result

    def _create_components(self) -> None:
        # Register skybrush.server.ext as an entry point group that is used to
        # discover extensions
        self.extension_manager.module_finder.add_entry_point_group(
            "skybrush.server.ext"
        )

        # Log requests to restart an extension
        self.extension_manager.restart_requested.connect(
            self._on_restart_requested, sender=self.extension_manager
        )

        # Create an object that can be used to get hold of commonly used
        # directories within the app
        self.dirs = AppDirs("Skybrush Server", "CollMot Robotics")

        # Create an object to hold information about all the registered
        # communication channel types that the server can handle
        self.channel_type_registry = ChannelTypeRegistry()

        # Create an object to hold information about all the connected
        # clients that the server can talk to
        self.client_registry = ClientRegistry(self.channel_type_registry)
        self.client_registry.count_changed.connect(
            self._on_client_count_changed, sender=self.client_registry
        )

        # Create an object that keeps track of commands being executed
        # asynchronously on remote UAVs
        self.command_execution_manager = CommandExecutionManager()
        self.command_execution_manager.progress_updated.connect(
            self._on_command_execution_progress_updated,
            sender=self.command_execution_manager,
        )
        self.command_execution_manager.suspended.connect(
            self._on_command_execution_suspended,
            sender=self.command_execution_manager,
        )
        self.command_execution_manager.expired.connect(
            self._on_command_execution_timeout, sender=self.command_execution_manager
        )
        self.command_execution_manager.finished.connect(
            self._on_command_execution_finished, sender=self.command_execution_manager
        )

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

        # Create an object that keeps track of registered UAV drivers
        self.uav_driver_registry = UAVDriverRegistry()

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
            "SYS-MSG", BatchMessageRateLimiter(self.create_SYS_MSG_message_from)
        )
        self.rate_limiters.register(
            "UAV-INF", UAVMessageRateLimiter(self.create_UAV_INF_message_for)
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
        self.device_tree_subscriptions = DeviceTreeSubscriptionManager(
            self.device_tree,
            client_registry=self.client_registry,
            message_hub=self.message_hub,
        )

        # Ask the extension manager to scan the entry points for user-defined
        # extensions and plugins
        self.extension_manager.rescan()

    def _find_connection_by_id(
        self,
        connection_id: str,
        response: Optional[Union[FlockwaveResponse, FlockwaveNotification]] = None,
    ) -> Optional[ConnectionRegistryEntry]:
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
        self,
        object_id: str,
        response: Optional[Union[FlockwaveResponse, FlockwaveNotification]] = None,
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

    def _on_client_count_changed(self, sender: ClientRegistry) -> None:
        """Handler called when the number of clients attached to the server
        has changed.
        """
        if self.extension_manager:
            self.run_in_background(
                self.extension_manager.set_spinning, self.num_clients > 0
            )

    def _on_connection_state_changed(
        self,
        sender: ConnectionRegistry,
        entry: ConnectionRegistryEntry,
        old_state: ConnectionState,
        new_state: ConnectionState,
    ) -> None:
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

    def _on_command_execution_finished(
        self, sender: CommandExecutionManager, status: CommandExecutionStatus
    ) -> None:
        """Handler called when the execution of a remote asynchronous
        command finished. Dispatches an appropriate ``ASYNC-RESP`` message.

        Parameters:
            sender: the command execution manager
            status: the status object corresponding to the command whose
                execution has just finished.
        """
        body = {"type": "ASYNC-RESP", "id": status.id}

        if status.error:
            body["error"] = (
                str(status.error)
                if not hasattr(status.error, "json")
                else status.error.json  # type: ignore
            )
        else:
            body["result"] = status.result

        message = self.message_hub.create_response_or_notification(body)
        for client_id in status.clients_to_notify:
            self.message_hub.enqueue_message(message, to=client_id)

    def _on_command_execution_progress_updated(
        self, sender: CommandExecutionManager, status: CommandExecutionStatus
    ) -> None:
        """Handler called when the progress of the execution of a remote
        asynchronous command is updated. Dispatches an appropriate
        ``ASYNC-ST`` message.

        Parameters:
            sender: the command execution manager
            status: the status object corresponding to the command whose
                execution has just finished.
        """
        body = {
            "type": "ASYNC-ST",
            "id": status.id,
            "progress": status.progress.json,  # type: ignore
        }
        if status.is_suspended:
            body["suspended"] = True
        message = self.message_hub.create_response_or_notification(body)
        for client_id in status.clients_to_notify:
            self.message_hub.enqueue_message(message, to=client_id)

    _on_command_execution_suspended = _on_command_execution_progress_updated

    def _on_command_execution_timeout(
        self,
        sender: CommandExecutionManager,
        statuses: Iterable[CommandExecutionStatus],
    ) -> None:
        """Handler called when the execution of a remote asynchronous
        command was abandoned with a timeout. Dispatches an appropriate
        ``ASYNC-TIMEOUT`` message.

        Parameters:
            sender: the command execution manager
            statuses: the status objects corresponding to the commands whose
                execution has timed out.
        """
        # Multiple commands may have timed out at the same time, and we
        # need to sort them by the clients that originated these requests
        # so we can dispatch individual ASYNC-TIMEOUT messages to each of
        # them
        receipt_ids_by_clients: defaultdict[str, list[str]] = defaultdict(list)
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

    def _on_restart_requested(self, sender, name: str) -> None:
        """Handler called when an extension requests the server to restart
        itself.
        """
        self.log.warning(
            "The server should be restarted in order for the changes to take effect",
            extra={"id": name},
        )

    def _process_configuration(self, config: Configuration) -> Optional[int]:
        # Process the configuration options
        cfg = config.get("COMMAND_EXECUTION_MANAGER", {})
        self.command_execution_manager.timeout = cfg.get("timeout", 90)

        # Override the base port if needed
        port_from_env: Optional[str] = environ.get("PORT")
        port: Optional[int] = config.get("PORT")
        if port_from_env:
            try:
                port = int(port_from_env)
            except ValueError:
                pass
        if port is not None:
            set_base_port(port)

        # Force-load the ext_manager and the licensing extension
        cfg = config.setdefault("EXTENSIONS", {})
        cfg["ext_manager"] = {}
        cfg["license"] = {}

    def _setup_app_configurator(self, configurator: AppConfigurator) -> None:
        configurator.key_filter = str.isupper
        configurator.merge_keys = ["EXTENSIONS"]
        configurator.safe = is_packaged()


############################################################################

app = SkybrushServer("skybrush", PACKAGE_NAME)

# ######################################################################## #


@app.message_hub.on("ASYNC-CANCEL")
def handle_ASYNC_CANCEL(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.cancel_async_operations(message.get_ids(), in_response_to=message)


@app.message_hub.on("ASYNC-RESUME")
def handle_ASYNC_RESUME(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.resume_async_operations(
        message.get_ids(), message.body.get("values") or {}, in_response_to=message
    )


@app.message_hub.on("CONN-INF")
def handle_CONN_INF(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_CONN_INF_message_for(message.get_ids(), in_response_to=message)


@app.message_hub.on("CONN-LIST")
def handle_CONN_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"ids": list(app.connection_registry.ids)}


@app.message_hub.on("DEV-INF")
def handle_DEV_INF(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_DEV_INF_message_for(message.body["paths"], in_response_to=message)


@app.message_hub.on("DEV-LIST")
def handle_DEV_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_DEV_LIST_message_for(message.get_ids(), in_response_to=message)


@app.message_hub.on("DEV-LISTSUB")
def handle_DEV_LISTSUB(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_DEV_LISTSUB_message_for(
        client=sender,
        path_filter=message.body.get("pathFilter", ("/",)),
        in_response_to=message,
    )


@app.message_hub.on("DEV-SUB")
def handle_DEV_SUB(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_DEV_SUB_message_for(
        client=sender,
        paths=message.body["paths"],
        lazy=bool(message.body.get("lazy")),
        in_response_to=message,
    )


@app.message_hub.on("DEV-UNSUB")
def handle_DEV_UNSUB(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_DEV_UNSUB_message_for(
        client=sender,
        paths=message.body["paths"],
        in_response_to=message,
        remove_all=message.body.get("removeAll", False),
        include_subtrees=message.body.get("includeSubtrees", False),
    )


@app.message_hub.on("OBJ-LIST")
def handle_OBJ_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    filter = message.body.get("filter")
    if filter is None:
        it = app.object_registry.ids
    else:
        it = app.object_registry.ids_by_types(filter)
    return {"ids": list(it)}


@app.message_hub.on("SYS-PING")
def handle_SYS_PING(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return hub.acknowledge(message)


@app.message_hub.on("SYS-PORTS")
def handle_SYS_PORTS(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"ports": dict(get_port_map())}


@app.message_hub.on("SYS-TIME")
async def handle_SYS_TIME(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    adjustment = message.body.get("adjustment")
    if adjustment is not None:
        adjustment = float(adjustment)
        allowed, reason = await can_set_system_time_detailed_async()
        if not allowed:
            return hub.acknowledge(
                message, outcome=False, reason=f"Permission denied. {reason}"
            )

        if adjustment != 0:
            # This branch is required so the client can test whether time
            # adjustments are supported by sending an adjustment with zero delta
            adjusted_time_msec = get_system_time_msec() + adjustment
            try:
                await set_system_time_msec_async(adjusted_time_msec)
            except Exception as ex:
                return hub.acknowledge(message, outcome=False, reason=str(ex))

    return {"timestamp": get_system_time_msec()}


@app.message_hub.on("SYS-VER")
def handle_SYS_VER(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"software": "skybrushd", "version": server_version}


@app.message_hub.on("UAV-INF")
def handle_UAV_INF(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return app.create_UAV_INF_message_for(message.get_ids(), in_response_to=message)


@app.message_hub.on("UAV-LIST")
def handle_UAV_LIST(message: FlockwaveMessage, sender: Client, hub: MessageHub):
    return {"ids": list(app.object_registry.ids_by_type(UAV))}


@app.message_hub.on("LOG-DATA")
async def handle_single_uav_operations(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
):
    if message.get_type() == "LOG-DATA":
        id_property = "uavId"
    else:
        id_property = "id"
    return await app.dispatch_to_uav(message, sender, id_property=id_property)


@app.message_hub.on(
    "LOG-INF",
    "OBJ-CMD",
    "PRM-GET",
    "PRM-SET",
    "PRM-SET-MANY",
    "UAV-CALIB",
    "UAV-FLY",
    "UAV-HALT",
    "UAV-HOVER",
    "UAV-LAND",
    "UAV-MOTOR",
    "UAV-PREFLT",
    "UAV-RST",
    "UAV-RTH",
    "UAV-SLEEP",
    "UAV-SIGNAL",
    "UAV-TAKEOFF",
    "UAV-TEST",
    "UAV-VER",
    "UAV-WAKEUP",
)
async def handle_multi_uav_operations(
    message: FlockwaveMessage, sender: Client, hub: MessageHub
):
    return await app.dispatch_to_uavs(message, sender)


# ######################################################################## #
