"""Extension that provides a clock registry to the app and adds support
for the ``CLK-...`` commands defined in the Flockwave protocol.
"""

from ..registries import ClockRegistry, find_in_registry

message_hub = None
registry = None

exports = {
    "register_clock": None,
    "registry": None,
    "unregister_clock": None
}


def create_CLK_INF_message_for(clock_ids, in_response_to=None):
    """Creates a CLK-INF message that contains information regarding
    the clocks with the given IDs.

    Parameters:
        clock_ids (iterable): list of clock IDs
        in_response_to (FlockwaveMessage or None): the message that the
            constructed message will respond to. ``None`` means that the
            constructed message will be a notification.

    Returns:
        FlockwaveMessage: the CLK-INF message with the status info of
            the given clocks
    """
    global message_hub

    statuses = {}

    body = {"status": statuses, "type": "CLK-INF"}
    response = message_hub.create_response_or_notification(
        body=body, in_response_to=in_response_to)

    for clock_id in clock_ids:
        entry = find_clock_by_id(clock_id, response)
        if entry:
            statuses[clock_id] = entry.json

    return response


def find_clock_by_id(clock_id, response=None):
    """Finds the clock with the given ID in the clock registry or registers
    a failure in the given response object if there is no clock with the
    given ID.

    Parameters:
        clock_id (str): the ID of the clock to find
        response (Optional[FlockwaveResponse]): the response in which
            the failure can be registered

    Returns:
        Optional[Clock]: the clock with the given ID or ``None`` if there
            is no such clock
    """
    global registry
    return find_in_registry(registry, clock_id, response, "No such clock")


def on_clock_changed(sender, clock):
    """Handler called when one of the clocks managed by the clock
    registry has changed. Creates and sends a ``CLK-INF`` notification for the
    clock that has changed.
    """
    global message_hub

    message = create_CLK_INF_message_for([clock.id])
    message_hub.send_message(message)


#############################################################################


def handle_CLK_INF(message, sender, hub):
    return create_CLK_INF_message_for(
        message.body["ids"], in_response_to=message
    )


def handle_CLK_LIST(message, sender, hub):
    global registry
    return {
        "ids": list(registry.ids)
    }


#############################################################################

def load(app, configuration, logger):
    global message_hub, registry

    message_hub = app.message_hub
    registry = ClockRegistry()

    message_hub.register_message_handler(handle_CLK_INF, "CLK-INF")
    message_hub.register_message_handler(handle_CLK_LIST, "CLK-LIST")

    registry.clock_changed.connect(on_clock_changed, sender=registry)

    exports.update(
        registry=registry,
        register_clock=registry.add,
        unregister_clock=registry.remove
    )


def unload(app):
    global message_hub, registry

    exports.update(
        registry=None,
        register_clock=None,
        unregister_clock=None
    )

    registry.clock_changed.disconnect(on_clock_changed, sender=registry)

    message_hub.unregister_message_handler(handle_CLK_INF, "CLK-INF")
    message_hub.unregister_message_handler(handle_CLK_LIST, "CLK-LIST")

    registry = None
    message_hub = None
