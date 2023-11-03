"""Extension that provides a clock registry to the app and adds support
for the ``CLK-...`` commands defined in the Skybrush protocol.
"""

from __future__ import annotations

from blinker import Signal
from contextlib import contextmanager, ExitStack
from trio import sleep_forever
from typing import Any, Iterator, Iterable, Optional, TYPE_CHECKING

from flockwave.server.model.clock import Clock
from flockwave.server.registries.base import RegistryBase, find_in_registry
from flockwave.server.utils.generic import overridden

if TYPE_CHECKING:
    from flockwave.server.message_hub import MessageHub
    from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse

message_hub: Optional["MessageHub"] = None
registry: Optional["ClockRegistry"] = None

exports: dict[str, Any] = {
    "register_clock": None,
    "registry": None,
    "unregister_clock": None,
    "use_clock": None,
}


class ClockRegistry(RegistryBase[Clock]):
    """Registry that contains information about all the clocks and timers
    managed by the server.

    The registry allows us to quickly retrieve information about a clock
    by its identifier, or the status of the clock (i.e. whether it is
    running or not and how it relates to the system time).
    """

    clock_changed = Signal()
    """Signal that is dispatched when one of the clocks registered in the clock
    registry is changed (adjusted), started or stopped. You need to subscribe to
    the signals of the clock on your own if you are interested in the exact
    signal that caused a ``clock_changed`` signal to be dispatched from the
    registry.
    """

    def add(self, clock: Clock) -> None:
        """Registers a clock in the registry.

        This function is a no-op if the clock is already registered.

        Parameters:
            clock: the clock to register

        Throws:
            KeyError: if the ID of the clock is already taken by a different clock
        """
        old_clock = self._entries.get(clock.id, None)
        if old_clock is not None and old_clock != clock:
            raise KeyError(f"Clock ID already taken: {clock.id}")
        self._entries[clock.id] = clock
        self._subscribe_to_clock(clock)

    def remove(self, clock: Clock) -> Optional[Clock]:
        """Removes the given clock from the registry.

        This function is a no-op if the clock is not registered.

        Parameters:
            clock: the clock to deregister

        Returns:
            the clock that was deregistered, or ``None`` if the clock was not
                registered
        """
        return self.remove_by_id(clock.id)

    def remove_by_id(self, clock_id: str) -> Optional[Clock]:
        """Removes the clock with the given ID from the registry.

        This function is a no-op if no clock is registered with the given ID.

        Parameters:
            clock_id: the ID of the clock to deregister

        Returns:
            the clock that was deregistered, or ``None`` if the clock was not
            registered
        """
        clock = self._entries.pop(clock_id, None)
        if clock:
            self._unsubscribe_from_clock(clock)
        return clock

    @contextmanager
    def use(self, clock: Clock) -> Iterator[Clock]:
        """Temporarily adds a new clock, hands control back to the caller in a
        context, and then removes the clock when the caller exits the context.

        Parameters:
            clock: the clock to add

        Yields:
            the clock object that was added
        """
        self.add(clock)
        try:
            yield clock
        finally:
            self.remove(clock)

    def _subscribe_to_clock(self, clock: Clock) -> None:
        """Subscribes to the signals of the given clock in order to
        redispatch them.
        """
        clock.changed.connect(self._send_clock_changed_signal, sender=clock)
        clock.started.connect(self._send_clock_changed_signal, sender=clock)
        clock.stopped.connect(self._send_clock_changed_signal, sender=clock)

    def _unsubscribe_from_clock(self, clock: Clock) -> None:
        """Unsubscribes from the signals of the given clock."""
        clock.changed.disconnect(self._send_clock_changed_signal, sender=clock)
        clock.started.disconnect(self._send_clock_changed_signal, sender=clock)
        clock.stopped.disconnect(self._send_clock_changed_signal, sender=clock)

    def _send_clock_changed_signal(self, sender, **kwds):
        """Sends a ``clock_changed`` signal in response to an actual
        ``started``, ``stopped`` or ``changed`` signal from one of the clocks
        in the registry. The ``clock`` argument of the signal being sent will
        refer to the clock that sent the original signal.
        """
        self.clock_changed.send(self, clock=sender)


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


def on_clock_changed(sender, clock: "Clock") -> None:
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
