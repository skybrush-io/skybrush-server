"""Global application-wide signaling mechanism that extensions can use to
communicate with each other in a coordinated manner without needing to import
each other's API.
"""

from __future__ import annotations

from blinker import NamedSignal, Signal
from contextlib import contextmanager, ExitStack
from logging import Logger
from typing import Callable, Dict, Iterator, Optional


#: Logger that will be used to log unexpected exceptions from signal handlers
log: Optional[Logger] = None

#: Namespace containing all the signals registered in this extension
signals: Optional["Namespace"] = None


class ProtectedSignal(NamedSignal):
    """Object that is mostly API-compatible with a standard Signal_ from the
    ``blinker`` module but shields the listeners from exceptions thrown from
    another listener.
    """

    def send(self, *sender, **kwargs):
        if len(sender) == 0:
            sender = None
        elif len(sender) > 1:
            raise TypeError(
                f"send() accepts only one positional argument, {len(sender)} given"
            )
        else:
            sender = sender[0]

        result = []
        if not self.receivers:
            return result

        for receiver in self.receivers_for(sender):
            try:
                retval = receiver(sender, **kwargs)
            except Exception as ex:
                if log:
                    log.exception("Unexpected exception caught in signal dispatch")
                retval = ex
            result.append((receiver, retval))

        return result


class Namespace(dict):
    """A mapping of signal names to signals."""

    def signal(self, name: str, doc: Optional[str] = None) -> ProtectedSignal:
        """Return the ProtectedSignal_ called *name*, creating it if required.

        Repeated calls to this function will return the same signal object.
        """
        try:
            return self[name]
        except KeyError:
            return self.setdefault(name, ProtectedSignal(name, doc))


def get_signal(name: str) -> Signal:
    """Returns the signal with the given name, registering it on-the-fly if
    needed.

    Parameters:
        name: the name of the signal

    Returns:
        the signal associated to the given name
    """
    global signals

    if signals is None:
        raise RuntimeError(
            "Attempted to get a signal reference when the extension is not running"
        )

    return signals.signal(name)


@contextmanager
def use_signals(map: Dict[str, Callable]) -> Iterator[None]:
    """Context manager that registers signal handler functions for multiple
    signals when entering the context and unregisters them when exiting the
    context.
    """
    with ExitStack() as stack:
        for key, func in map.items():
            signal = get_signal(key)
            stack.enter_context(signal.connected_to(func))  # type: ignore
        yield


def load(app, configuration, logger):
    global signals
    global log

    log = logger
    signals = Namespace()


def unload():
    global signals
    signals = None


description = "Signal emission and subscription service for intra-server communication"
exports = {"get": get_signal, "use": use_signals}
schema = {}
