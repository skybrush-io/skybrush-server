"""Extension that provides basic support for RC transmitters.

Actual RC transmitter implementations are to be provided by additional extensions
that depend on this one. The purpose of this extension is simply to provide a
signal that other extensions can subscribe to if they are interested in the
values of the RC channels.
"""

from typing import Any, ClassVar, List, Sequence


#: Signal that this extension emits in order to notify subscribers about the
#: new channel values
rc_changed_signal: Any = None

#: Stores whether the extension is in debug mode
debug: bool = False


#: Object that contains the current values of the RC channels. Must _not_ be
#: modified by other extensions.
class RCState(Sequence[int]):
    """Object holding the current values of the RC channels as well as the
    number of valid channels.

    Raw channel values are represented in the range [0; 65535]. Negative numbers
    denote invalid channels. The maximum number of supported RC channels is 18.

    Channel map is according to the default ArduPilot conventions: the first four
    channels are roll, pitch, throttle and yaw; the next channel is a flight
    mdoe switch. The remaining channels have no specific semantics.
    """

    #: Maximum number of channels supported by this object
    MAX_CHANNEL_COUNT: ClassVar[int] = 18

    #: Raw channel values as a list, exposed for performance. If you use this
    #: property directly, do NOT modify the list or assign a new instance to it.
    channels: List[int]

    #: Number of channels
    num_channels: int

    def __init__(self):
        """Constructor."""
        self.reset()

    def __getitem__(self, index: int) -> int:
        """Returns the raw, unscaled value of the RC channel with the given
        index.
        """
        return self.channels[index]

    def __len__(self):
        return len(self.channels)

    def get_scaled_channel_value(self, index: int) -> float:
        """Returns the value of the given RC channel, scaled into the [0; 1]
        range.
        """
        raw_value = self.channels[index]
        if raw_value < 0 or raw_value > 65535:
            return -1
        else:
            return raw_value / 65535

    def reset(self) -> None:
        """Invalidates all RC channels."""
        self.channels = [-1] * self.MAX_CHANNEL_COUNT
        self.num_channels = 0

    def update(self, values: Sequence[int]) -> None:
        """Updates the channel values of the object."""
        num_values = len(values)
        if num_values > self.MAX_CHANNEL_COUNT:
            self.channels[:] = values[: self.MAX_CHANNEL_COUNT]
            self.num_channels = self.MAX_CHANNEL_COUNT
        else:
            self.channels[:num_values] = values
            self.num_channels = num_values


#: Singleton instance of RCState
rc = RCState()


def load(app, configuration):
    global rc_changed_signal, debug

    signals = app.import_api("signals")
    rc_changed_signal = signals.get("rc:changed")

    debug = bool(configuration.get("debug"))
    if debug:
        rc_changed_signal.connect(print_debug_info)


def unload():
    global rc_changed_signal, debug

    if debug:
        rc_changed_signal.disconnect(print_debug_info)

    rc_changed_signal = None


def notify(values: Sequence[int]):
    """Function that is to be called by extensions implementing support for
    a particular RC protocol when they wish to update the values of the RC
    channels.
    """
    global rc
    rc.update(values)
    rc_changed_signal.send(rc)


def print_debug_info(sender: RCState) -> None:
    print("RC channels changed:", repr(sender.channels))


dependencies = ("signals",)
description = "RC transmitter support"
exports = {"notify": notify}
schema = {}
tags = "experimental"
