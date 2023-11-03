from inspect import isawaitable, isasyncgen
from typing import (
    Any,
    Callable,
    Iterable,
    Optional,
    Union,
    TypeVar,
    TYPE_CHECKING,
)

from .model import (
    Client,
    FlockwaveMessage,
    FlockwaveNotification,
    FlockwaveResponse,
)
from .registries import Registry, find_in_registry

if TYPE_CHECKING:
    from .commands import CommandExecutionManager
    from .message_hub import MessageHandler, MessageHub

__all__ = (
    "create_mapper",
    "create_multi_object_message_handler",
    "create_object_listing_request_handler",
    "transform_message_body",
    "MessageBodyTransformationSpec",
)

T = TypeVar("T")

GenericMapperMessageFactory = Callable[
    ["MessageHub", Iterable[str], Optional[FlockwaveMessage], Optional[Client]],
    Union[FlockwaveNotification, FlockwaveResponse],
]
"""Type alias for SOMETHING-INF-style Flockwave message factory functions"""

MessageBodyTransformationSpec = Union[
    Callable[[Any], Any], dict[str, Callable[[Any], Any]], None
]
"""Type alias for objects that specify how to transform the body of a
message before it is forwarded to a command handler function.

Objects of this type may be callables that can be called with a single
argument (the message body) and return an object that should be forwarded
to the command handler instead of the original message body, or may be
a dictionary mapping keys to functions, in which case the values corresponding
to the keys in the message body will be mapped individually according to the
functions specified in the dictionary.

The transformation object may also be ``None``, representing the identity
transformation.
"""

RegistryOrRegistryGetter = Union[Registry[T], Callable[[], Optional[Registry[T]]], None]
"""Type alias for registries or getter functions that return a registry when called
with no arguments.
"""


def create_mapper(
    type: str,
    registry_or_registry_getter: RegistryOrRegistryGetter[T],
    *,
    key: str = "result",
    filter: Optional[Callable[[T], bool]] = None,
    getter: Optional[Callable[[Any], Any]] = None,
    description: str = "item",
    add_object_id: bool = False,
    cmd_manager: Optional["CommandExecutionManager"] = None,
) -> GenericMapperMessageFactory:
    """Creates a standard SOMETHING-INF or SOMETHING-PROPS-style Flockwave
    message factory function.

    This function is a fairly complex Swiss army knife that can be used as a
    building block for creating message handler functions that retrieve
    _some information_ for multiple model objects from a registry, or do
    _something_ with multiple model objects from a registry.

    Message factory functions returned from this function are useful when
    constructing multi-object message handlers with
    `create_multi_object_response_handler()`. It is assumed that the
    original message contains a key named `ids` that lists multiple model
    objects that have to be looked up from an object registry. Furthermore,
    it is also assumed that you want to execute the same function on each of
    the objects, collect the results, and return them in a single response
    message, assigned to a key whose value maps the original object IDs to
    the retrieved values.

    More precisely, the returned factory function takes a message hub, a list
    of object IDs and an optional source message that the IDs originated from.
    Each object is looked up in the specified registry by ID. The factory then
    returns a message that contains a message type (specified by the `type`
    parameter, typically identical to the message being responded to) and
    _another_, named key (specified by the `key` parameter) whose _value_ maps
    the received object IDs to the corresponding status information, fetched
    from the objects themselves using the specified getter function.

    The returned message may also contain a key named `error`, which maps the IDs
    of the objects for which the retrieval failed to error messages explaining
    the failure.

    Async getters are also supported; if a getter is async, the returned message
    will _not_ contain the specified `key` but it will contain a key named
    `receipts` that map each input object ID for which the async getter was
    invoked to a receipt ID and the results will be posted later by the server
    to the client in `ASYNC-...` messages. Note that async getters may only be
    used if the message factory receives an incoming request from the client to
    respond to.

    Parameters:
        type: the Flockwave _type_ of the message to produce. (The value of the
            `type` key in the response body).
        registry_or_registry_getter: the registry in which the objects are
            being looked up, or a callable that returns a registry when invoked
            with no arguments
        key: the name of the key in the Flockwave message body where the
            retrieved information about the objects will be placed
        filter: a predicate that the item from the registry matching a given
            ID will be called with. Only items for which the predicate returns
            `True` are allowed to be returned to the user; items for which the
            predicate returns `False` are treated as nonexistent
        getter: a function that takes a matched object from the registry as its
            only argument, and extracts or calculates the piece of information
            to return. It may also have side effects and may simply return
            `True` or `None` or something similar if the primary goal is to
            perform a side effect.
        description: a textual, human-readable description of the item type
            being looked up in this function. Used in error messages. Must be
            lowercase.
        add_object_id: whether to extend the objects returned from the getter
            with the ID of the object that was queried, _if_ and only if the
            getter returns a dictionary.
        cmd_manager: the async command execution manager of the server; required
            if (and only if) the getter is an async function or generator

    Returns:
        the message handler function
    """
    registry_is_deferred = callable(registry_or_registry_getter)

    def factory(
        hub: "MessageHub",
        ids: Iterable[str],
        in_response_to: Optional[FlockwaveMessage] = None,
        sender: Optional[Client] = None,
    ) -> Union[FlockwaveNotification, FlockwaveResponse]:
        results = {}

        body: dict[str, Any] = {"type": type}
        body[key] = results
        response = hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        registry: Registry[T] = (
            registry_or_registry_getter()  # type: ignore
            if registry_is_deferred
            else registry_or_registry_getter
        )

        # Look up each object and perform the operation on them
        for object_id in ids:
            object = find_in_registry(
                registry,
                object_id,
                predicate=filter,
                response=response,
                failure_reason=f"No such {description}",
            )
            if not object:
                # Failure registered already by find_in_registry()
                continue

            # Execute the getter (if any) and catch all runtime errors
            error, result = None, None
            if getter is not None:
                result = getter(object)
            else:
                result = object

            # If the returned result was an exception, convert it to an error
            if isinstance(result, Exception):
                error = result
                result = None

            # Update the response
            if error is not None:
                if isinstance(response, FlockwaveResponse):
                    response.add_error(object_id, error)
                else:
                    # This is only a notification so we can ignore the error
                    pass
            elif isawaitable(result) or isasyncgen(result):
                if isinstance(response, FlockwaveResponse):
                    if cmd_manager is None:
                        response.add_error(
                            object_id,
                            "async operations not supported without a command "
                            "execution manager",
                        )
                    elif sender is None:
                        response.add_error(
                            object_id, "async operations not supported without a sender"
                        )
                    else:
                        receipt = cmd_manager.new(client_to_notify=sender.id)
                        response.add_receipt(object_id, receipt)
                        response.when_sent(
                            cmd_manager.mark_as_clients_notified, receipt.id, result
                        )
                else:
                    # This is only a notification so we need to throw an error
                    raise RuntimeError("async getters not supported for notifications")
            else:
                if add_object_id and getter and isinstance(result, dict):
                    result["id"] = object_id
                results[object_id] = result

        return response

    return factory


def create_multi_object_message_handler(
    factory: GenericMapperMessageFactory,
) -> "MessageHandler":
    """Creates a standard SOMETHING-INF-style Flockwave message handler that
    looks up objects from an object registry by ID and returns some
    information about them in a response.

    The lookup is performed by a message factory; see `create_mapper()` for
    more information about how to construct it.

    It is assumed that the incoming message contains a single `ids` key that
    is an array of strings, each array representing an ID of an object to look
    up. It is also assumed that the response body is shaped like a typical
    ...-INF message response according to the Flockwave specs.

    Parameters:
        factory: the message factory to use to construct the response

    Returns:
        the message handler that can be registered in the message hub.
    """

    def handler(
        message: FlockwaveMessage, sender: Client, hub: "MessageHub"
    ) -> FlockwaveResponse:
        return factory(hub, message.get_ids(), message, sender)  # type: ignore

    return handler


def create_object_listing_request_handler(
    registry_or_registry_getter: RegistryOrRegistryGetter[T],
) -> "MessageHandler":
    """Creates a standard SOMETHING-INF or SOMETHING-PROPS-style Flockwave
    message factory function.

    The returned factory function takes a message hub, a list of object IDs and
    an optional source message that the IDs originated from. It must return an
    object that maps the received object IDs to the corresponding status
    information, fetched using the specified getter function.

    Parameters:
        type: the Flockwave type of the message to produce
        registry_or_registry_getter: the registry from which the object IDs are
            to be listed, or a callable that returns a registry when invoked
            with no arguments

    Returns:
        the message factory function
    """
    registry_is_deferred = callable(registry_or_registry_getter)

    def handler(
        message: FlockwaveMessage,
        sender: "Client",
        hub: "MessageHub",
    ):
        registry: Registry[T] = (
            registry_or_registry_getter()  # type: ignore
            if registry_is_deferred
            else registry_or_registry_getter
        )
        return {"ids": list(registry.ids)}

    return handler


def transform_message_body(
    transformer: MessageBodyTransformationSpec, body: dict[str, Any]
) -> dict[str, Any]:
    """Helper function that executes the given transformation specification
    on the given message body.

    Note that the function may mutate the message body.

    Parameters:
        transformer: a message body transformation specification object that
            tells us how to extract an object of relevance from the body
            of a protocol message

    Returns:
        the object that the transformer extracted from the message body; may
        be the same object as the message itself or may be a different one,
        depending on the transformation itself
    """
    if transformer is None:
        return body

    if callable(transformer):
        return transformer(body)

    for parameter_name, func in transformer.items():
        if parameter_name in body:
            value = body[parameter_name]
            body[parameter_name] = func(value)

    return body
