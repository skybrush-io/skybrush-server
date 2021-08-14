"""Extension that provides a clock registry to the app and adds support
for the ``CLK-...`` commands defined in the Skybrush protocol.
"""

from __future__ import annotations

from contextlib import ExitStack
from trio import sleep_forever
from typing import Any, Dict, Iterable, Optional, TYPE_CHECKING

from flockwave.server.utils.generic import overridden

from ..registries import ClockRegistry, find_in_registry

if TYPE_CHECKING:
    from flockwave.server.message_hub import MessageHub
    from flockwave.server.model.clock import Clock
    from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse

message_hub: Optional["MessageHub"] = None
registry: Optional["ClockRegistry"] = None

exports: Dict[str, Any] = {
    "register_clock": None,
    "registry": None,
    "unregister_clock": None,
    "use_clock": None,
}


def create_CLK_INF_message_for(
    clock_ids: Iterable[str], in_response_to: Optional["FlockwaveMessage"] = None
) -> "FlockwaveMessage":
    """Creates a CLK-INF message that contains information regarding
    the clocks with the given IDs.

    Parameters:
        clock_ids: list of clock IDs
        in_response_to: the message that the constructed message will respond
            to. ``None`` means that the constructed message will be a
            notification.

    Returns:
        the CLK-INF message with the status info of the given clocks
    """
    global message_hub

    assert message_hub is not None

    statuses = {}

    body = {"status": statuses, "type": "CLK-INF"}
    response = message_hub.create_response_or_notification(
        body=body, in_response_to=in_response_to
    )

    for clock_id in clock_ids:
        entry = find_clock_by_id(clock_id, response)  # type: ignore
        if entry:
            statuses[clock_id] = entry.json

    return response


def find_clock_by_id(
    clock_id: str, response: Optional["FlockwaveResponse"] = None
) -> Optional[Clock]:
    """Finds the clock with the given ID in the clock registry or registers
    a failure in the given response object if there is no clock with the
    given ID.

    Parameters:
        clock_id: the ID of the clock to find
        response: the response in which the failure can be registered

    Returns:
        the clock with the given ID or ``None`` if there is no such clock
    """
    global registry
    return find_in_registry(
        registry, clock_id, response=response, failure_reason="No such clock"
    )


def on_clock_changed(sender, clock):
    """Handler called when one of the clocks managed by the clock
    registry has changed. Creates and sends a ``CLK-INF`` notification for the
    clock that has changed.
    """
    global message_hub

    assert message_hub is not None

    message = create_CLK_INF_message_for([clock.id])
    message_hub.enqueue_message(message)


#############################################################################


def handle_CLK_INF(message, sender, hub):
    return create_CLK_INF_message_for(message.get_ids(), in_response_to=message)


def handle_CLK_LIST(message, sender, hub):
    global registry

    assert registry is not None

    return {"ids": list(registry.ids)}


#############################################################################


async def run(app, configuration, logger):
    message_hub = app.message_hub
    registry = ClockRegistry()

    handlers = {"CLK-INF": handle_CLK_INF, "CLK-LIST": handle_CLK_LIST}

    with ExitStack() as stack:
        stack.enter_context(
            overridden(globals(), message_hub=message_hub, registry=registry)
        )
        stack.enter_context(
            registry.clock_changed.connected_to(on_clock_changed, sender=registry)  # type: ignore
        )
        stack.enter_context(
            overridden(
                exports,
                registry=registry,
                register_clock=registry.add,
                unregister_clock=registry.remove,
                use_clock=registry.use,
            )
        )
        stack.enter_context(message_hub.use_message_handlers(handlers))
        await sleep_forever()


description = "Clocks and clock registry"
schema = {}
