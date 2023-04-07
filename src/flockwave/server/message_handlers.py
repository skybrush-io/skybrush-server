from typing import (
    Any,
    Callable,
    Dict,
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
    from .message_hub import MessageHandler, MessageHub

__all__ = (
    "create_generic_INF_or_PROPS_message_factory",
    "create_multi_object_message_handler",
    "transform_message_body",
    "MessageBodyTransformationSpec",
)

GenericINFMessageFactory = Callable[
    ["MessageHub", Iterable[str], Optional[FlockwaveMessage]],
    Union[FlockwaveNotification, FlockwaveResponse],
]
"""Type alias for SOMETHING-INF-style Flockwave message factory functions"""

MessageBodyTransformationSpec = Union[
    Callable[[Any], Any], Dict[str, Callable[[Any], Any]], None
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

T = TypeVar("T")


def create_generic_INF_or_PROPS_message_factory(
    type: str,
    key: str,
    registry: Registry[T],
    *,
    filter: Optional[Callable[[T], bool]] = None,
    getter: Optional[Callable[[Any], Any]] = None,
    description: str = "item",
    add_object_id: bool = False,
) -> GenericINFMessageFactory:
    """Creates a standard SOMETHING-INF or SOMETHING-PROPS-style Flockwave
    message factory function.

    The returned factory function takes a message hub, a list of object IDs and
    an optional source message that the IDs originated from. It must return an
    object that maps the received object IDs to the corresponding status
    information, fetched using the specified getter function.

    Parameters:
        type: the Flockwave type of the message to produce
        key: the name of the key in the Flockwave message body where the
            information about the objects will be placed
        registry: the registry in which the objects are being looked up
        filter: a predicate that the item from the registry matching a given
            ID will be called with. Only items for which the predicate returns
            `True` are allowed to be returned to the user; items for which the
            predicate returns `False` are treated as nonexistent
        getter: a function that takes a matched item from the registry and
            extracts the status information to return
        description: a textual, human-readable description of the item type
            being looked up in this function. Used in error messages. Must be
            lowercase.
        add_object_id: whether to extend the objects returned from the getter
            with the ID of the object that was queried

    Returns:
        the message factory function
    """

    def factory(
        hub: "MessageHub",
        ids: Iterable[str],
        in_response_to: Optional[FlockwaveMessage] = None,
    ) -> Union[FlockwaveNotification, FlockwaveResponse]:
        statuses = {}

        body: Dict[str, Any] = {"type": type}
        body[key] = statuses
        response = hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for item_id in ids:
            item = find_in_registry(
                registry,
                item_id,
                predicate=filter,
                response=response,
                failure_reason=f"No such {description}",
            )
            if item:
                result = getter(item) if getter else item  # type: ignore
                if add_object_id and getter and isinstance(result, dict):
                    result["id"] = item_id
                statuses[item_id] = result
        return response

    return factory


def create_multi_object_message_handler(
    factory: GenericINFMessageFactory,
) -> "MessageHandler":
    """Creates a standard SOMETHING-INF-style Flockwave message handler that
    looks up objects from an object registry by ID and returns some
    information about them in a response.

    The lookup is performed by a message factory; see
    `create_generic_INF_or_PROPS_message_factory()` for more information about how to
    construct it.

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
        return factory(hub, message.get_ids(), in_response_to=message)  # type: ignore

    return handler


def transform_message_body(
    transformer: MessageBodyTransformationSpec, body: Dict[str, Any]
) -> Dict[str, Any]:
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

    for parameter_name, transformer in transformer.items():
        if parameter_name in body:
            value = body[parameter_name]
            body[parameter_name] = transformer(value)

    return body
