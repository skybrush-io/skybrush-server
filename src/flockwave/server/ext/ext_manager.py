"""Extension that provides the commands that allow the users of the server
to load, unload and reconfigure extensions while the server is running.

This extension is special in the sense that it provides a mandatory part of
the Skybrush server protocol. Therefore, this extension is loaded even if it
is not mentioned explicitly in the server configuration, and it cannot be
unloaded by the user.

The extension dispatches the following events via the `events` extension:

- `ext_manager:status` when an extension is loaded or unloaded; clients may
  query the new state with `EXT-LIST` or `EXT-INF`.
- `ext_manager:config` when the configuration of an extension is changed;
  clients may query the new configuration with `EXT-CFG`.

The body of both events contains an ``ids`` key that lists the identifiers
of the extensions that were affected. Multiple events of the same type that are
dispatched in quick succession are merged into a single event whose ``ids``
list contains the union of the identifiers.
"""

from collections.abc import Sequence
from contextlib import ExitStack
from functools import partial, wraps
from inspect import iscoroutinefunction
from typing import Any, TypedDict

from flockwave.ext.manager import ExtensionManager
from trio import sleep_forever

from flockwave.server.ext.events import Dispatcher, EventsExtensionAPI
from flockwave.server.utils.generic import overridden

#############################################################################

_, _, _my_name = __name__.rpartition(".")


class ExtensionIdsEventBody(TypedDict):
    """Body of the events dispatched by this extension."""

    ids: list[str]


config_events: Dispatcher[ExtensionIdsEventBody] | None = None
"""Dispatcher for the `ext_manager:config` event."""

status_events: Dispatcher[ExtensionIdsEventBody] | None = None
"""Dispatcher for the `ext_manager:status` event."""


def merge_extension_ids(
    pending: Sequence[ExtensionIdsEventBody], body: ExtensionIdsEventBody | None
) -> Sequence[ExtensionIdsEventBody]:
    """Merging policy for the events of this extension that merges the
    pending events into a single event whose ``ids`` list contains the union
    of the extension identifiers of the events.
    """
    ids: set[str] = set()
    for item in pending:
        ids.update(item["ids"])
    if body is not None:
        ids.update(body["ids"])
    return [{"ids": sorted(ids)}]


def for_each_id(func, key="status", has_param=False):
    """Decorator for message handler functions that extend the handler in a way
    that the handler has to be bothered only with a single ID when multiple
    IDs were submitted in the message.
    """
    global _my_name

    if has_param:

        def get_ids_and_params(message):
            return message.body.get("ids", {}).items()

    else:

        def get_ids_and_params(message):
            for extension_id in message.body.get("ids", []):
                yield extension_id, None

    if iscoroutinefunction(func):

        @wraps(func)
        async def wrapper(ext, message, sender, hub):
            status = {}
            body = {key: status}
            response = hub.create_response_or_notification(
                body=body, in_response_to=message
            )

            for extension_id, param in get_ids_and_params(message):
                if extension_id == _my_name:
                    response.add_error(extension_id, "Extension is protected")
                else:
                    try:
                        if has_param:
                            result = await func(ext, extension_id, param)
                        else:
                            result = await func(ext, extension_id)
                    except Exception as ex:
                        response.add_error(extension_id, ex)
                    else:
                        status[extension_id] = result

            return response

    else:

        @wraps(func)
        def wrapper(ext, message, sender, hub):
            status = {}
            body = {key: status}
            response = hub.create_response_or_notification(
                body=body, in_response_to=message
            )

            for extension_id, param in get_ids_and_params(message):
                if extension_id == _my_name:
                    response.add_error(extension_id, "Extension is protected")
                else:
                    try:
                        if has_param:
                            result = func(ext, extension_id, param)
                        else:
                            result = func(ext, extension_id)
                    except Exception as ex:
                        response.add_error(extension_id, ex)
                    else:
                        status[extension_id] = result

            return response

    return wrapper


def only_if_satisfies_predicate(func, predicate, error):
    """Decorator that takes the given function that is assumed to accept an
    extension manager in the first argument and an extension ID in the second
    argument, and wraps it in a way that the function is executed only if a
    predicate (which is also called with the extension manager and the
    extension ID) evaluates to True.
    """

    if iscoroutinefunction(func):

        @wraps(func)
        async def wrapper(ext, extension_id, *args, **kwds):
            if predicate(ext, extension_id):
                return await func(ext, extension_id, *args, **kwds)
            else:
                raise RuntimeError(error)

    else:

        @wraps(func)
        def wrapper(ext, extension_id, *args, **kwds):
            if predicate(ext, extension_id):
                return func(ext, extension_id, *args, **kwds)
            else:
                raise RuntimeError(error)

    return wrapper


def only_if_exists(func, error="No such extension"):
    """Decorator that takes the given function that is assumed to accept an
    extension manager in the first argument and an extension ID in the second
    argument, and wraps it in a way that the function is executed only if the
    extension is known to the extension manager (even if it does not exist
    yet).
    """

    def predicate(ext, id):
        return ext.exists(id)

    return only_if_satisfies_predicate(func, predicate, error)


def only_if_loaded(func, error="Extension is not loaded yet"):
    """Decorator that takes the given function that is assumed to accept an
    extension manager in the first argument and an extension ID in the second
    argument, and wraps it in a way that the function is executed only if the
    extension is loaded.
    """

    def predicate(ext, id):
        return ext.is_loaded(id)

    return only_if_satisfies_predicate(func, predicate, error)


def only_if_not_loaded(func, error="Extension is loaded already"):
    """Decorator that takes the given function that is assumed to accept an
    extension manager in the first argument and an extension ID in the second
    argument, and wraps it in a way that the function is executed only if the
    extension is NOT loaded.
    """

    def predicate(ext, id):
        return not ext.is_loaded(id)

    return only_if_satisfies_predicate(func, predicate, error)


def create_status_object(ext: ExtensionManager, identifier: str):
    """Creates a status object in JSON format for the extension with the given
    name.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension

    Returns:
        the status object of the extension
    """
    return {"id": identifier, "name": identifier, "loaded": ext.is_loaded(identifier)}


def get_configuration_of_extension(ext: ExtensionManager, identifier: str):
    """Creates an object that represents the last known configuration of the
    extension with the given name.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension

    Returns:
        the configuration object of the extension
    """
    return ext.get_configuration_snapshot(identifier)


async def load_extension(ext: ExtensionManager, identifier: str):
    """Loads the given extension into the extension manager.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension

    Returns:
        an empty object if the extension was loaded successfully

    Raises:
        RuntimeError: if the extension cannot be loaded
    """
    await ext.load(identifier)
    return {}


async def reload_extension(ext: ExtensionManager, identifier: str):
    """Reloads the given extension in the extension manager.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension

    Returns:
        an empty object if the extension was reloaded successfully

    Raises:
        RuntimeError: if the extension cannot be unloaded
    """
    await ext.reload(identifier)
    return {}


async def set_configuration_of_extension(
    ext: ExtensionManager, identifier: str, configuration: dict
):
    """Sets the configuration of the given extension in the extension manager.

    Note that the new configuration values do not take effect until the
    extension is reloaded.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension
        configuration: the new configuration of the extension

    Returns:
        an empty object if the extension was configured successfully

    Raises:
        RuntimeError: if the extension cannot be configured
    """
    ext.configure(identifier, configuration)
    if config_events:
        config_events.dispatch({"ids": [identifier]})
    return {}


async def unload_extension(ext: ExtensionManager, identifier: str):
    """Unloads the given extension from the extension manager.

    Parameters:
        ext: the extension manager
        identifier: the identifier of the extension

    Returns:
        an empty object if the extension was unloaded successfully

    Raises:
        RuntimeError: if the extension cannot be unloaded
    """
    await ext.unload(identifier)
    return {}


handle_EXT_CFG = for_each_id(only_if_exists(get_configuration_of_extension))
handle_EXT_LOAD = for_each_id(only_if_not_loaded(load_extension))
handle_EXT_INF = for_each_id(only_if_exists(create_status_object))
handle_EXT_RELOAD = for_each_id(only_if_loaded(reload_extension))
handle_EXT_SETCFG = for_each_id(
    only_if_exists(set_configuration_of_extension), has_param=True
)
handle_EXT_UNLOAD = for_each_id(only_if_loaded(unload_extension))


def handle_EXT_LIST(ext, message, sender, hub):
    global _my_name

    loaded = ext.loaded_extensions
    try:
        loaded.remove(_my_name)
    except ValueError:
        pass

    return {"loaded": loaded, "available": []}


#############################################################################


def on_extension_loaded_or_unloaded(sender, *, name: str, **kwds: Any) -> None:
    """Handler that is called when an extension is loaded or unloaded by the
    extension manager, regardless of whether it was triggered by a client.
    """
    if status_events and name != _my_name:
        status_events.dispatch({"ids": [name]})


async def run(app, configuration, logger):
    ext_manager = app.extension_manager
    events = app.import_api("events", EventsExtensionAPI)

    handlers = {
        "EXT-CFG": handle_EXT_CFG,
        "EXT-INF": handle_EXT_INF,
        "EXT-LOAD": handle_EXT_LOAD,
        "EXT-LIST": handle_EXT_LIST,
        "EXT-RELOAD": handle_EXT_RELOAD,
        "EXT-SETCFG": handle_EXT_SETCFG,
        "EXT-UNLOAD": handle_EXT_UNLOAD,
    }

    handlers = {key: partial(func, ext_manager) for key, func in handlers.items()}

    with ExitStack() as stack:
        stack.enter_context(
            overridden(
                globals(),
                config_events=stack.enter_context(
                    events.use("ext_manager:config", merging_policy=merge_extension_ids)
                ),
                status_events=stack.enter_context(
                    events.use("ext_manager:status", merging_policy=merge_extension_ids)
                ),
            )
        )
        stack.enter_context(
            ext_manager.loaded.connected_to(
                on_extension_loaded_or_unloaded, sender=ext_manager
            )
        )
        stack.enter_context(
            ext_manager.unloaded.connected_to(
                on_extension_loaded_or_unloaded, sender=ext_manager
            )
        )
        stack.enter_context(app.message_hub.use_message_handlers(handlers))
        await sleep_forever()


dependencies = ("events",)
description = "Provides support for managing extensions while the server is running"
schema = {}
tags = ("system",)
