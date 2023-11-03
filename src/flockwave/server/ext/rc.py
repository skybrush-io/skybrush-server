"""Extension that provides basic support for RC transmitters.

Actual RC transmitter implementations are to be provided by additional extensions
that depend on this one. The purpose of this extension is simply to provide a
signal that other extensions can subscribe to if they are interested in the
values of the RC channels.
"""

from logging import Logger

from typing import Any, ClassVar, Optional, Sequence


rc_changed_signal: Any = None
"""Signal that this extension emits in order to notify subscribers about the
new channel values.
"""

debug: bool = False
"""Stores whether the extension is in debug mode"""

logger: Optional[Logger] = None
"""Logger instance used by the extension"""


class RCState(Sequence[int]):
    """Object holding the current values of the RC channels as well as the
    number of valid channels.

    Raw channel values are represented in the range [0; 65535]. Negative numbers
    denote invalid channels. The maximum number of supported RC channels is 18.

    Channel map is according to the default ArduPilot conventions: the first four
    channels are roll, pitch, throttle and yaw; the next channel is a flight
    mdoe switch. The remaining channels have no specific semantics.
    """

    MAX_CHANNEL_COUNT: ClassVar[int] = 18
    """Maximum number of channels supported by this object"""

    channels: list[int]
    """Raw channel values as a list, exposed for performance. If you use this
    property directly, do NOT modify the list or assign a new instance to it.
    """

    num_channels: int
    """Number of channels that are actually used from the raw `channels` list.
    Zero means that RC reception is assumed to be lost.
    """

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

    def get_scaled_channel_value(
        self, index: int, min: float = 0, span: float = 1, out_of_range: float = -1
    ) -> float:
        """Returns the value of the given RC channel, scaled into a given
        range.

        Parameters:
            min: the minimum value in the output range
            span: the length of the output range
            out_of_range: the value to return for invalid RC channel values
        """
        raw_value = self.channels[index]
        if raw_value < 0 or raw_value > 65535:
            return out_of_range
        else:
            return min + span * (raw_value / 65535)

    def get_scaled_channel_values(
        self, min: float = 0, span: float = 1, out_of_range: float = -1
    ) -> list[float]:
        """Returns the value of all RC channels, scaled into a given
        range.

        Parameters:
            min: the minimum value in the output range
            span: the length of the output range
            out_of_range: the value to return for invalid RC channel values
        """
        result: list[float] = []

        for raw_value in self.channels:
            if raw_value < 0 or raw_value > 65535:
                result.append(out_of_range)
            else:
                result.append(min + span * (raw_value / 65535))

        return result

    def get_scaled_channel_values_int(
        self, min: int = 1000, span: int = 1000, out_of_range: int = 0
    ) -> list[int]:
        """Returns the value of all RC channels, scaled into a given
        range, as integers.

        The defaults are set up so the output is suitable for PWM intervals in
        microseconds.

        Parameters:
            min: the minimum value in the output range
            span: the length of the output range
            out_of_range: the value to return for invalid RC channel values
        """
        result: list[int] = []

        for raw_value in self.channels:
            if raw_value < 0 or raw_value > 65535:
                result.append(out_of_range)
            else:
                result.append(min + round(span * (raw_value / 65535)))

        return result

    @property
    def lost(self) -> bool:
        """Returns whether the RC connection is assumed to be lost."""
        return self.num_channels <= 0

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


rc = RCState()
"""Singleton instance of RCState"""


def load(app, configuration, log):
    global rc_changed_signal, debug, logger

    logger = log

    signals = app.import_api("signals")
    rc_changed_signal = signals.get("rc:changed")

    debug = bool(configuration.get("debug"))
    if debug:
        rc_changed_signal.connect(print_debug_info)


def unload():
    global rc_changed_signal, debug, logger

    if debug:
        rc_changed_signal.disconnect(print_debug_info)

    rc_changed_signal = None
    logger = None


def notify(values: Sequence[int]):
    """Function that is to be called by extensions implementing support for
    a particular RC protocol when they wish to update the values of the RC
    channels.
    """
    global rc
    rc.update(values)
    rc_changed_signal.send(rc)


def notify_lost():
    """Function that is to be called by extensions implementing support for
    a particular RC protocol when they wish to report that RC connection was
    lost and all RC channels should be reset to invalid values.
    """
    global rc
    rc.reset()
    rc_changed_signal.send(rc)


def print_debug_info(sender: RCState) -> None:
    if logger:
        logger.info(f"RC channels changed: {sender.channels!r}")


dependencies = ("signals",)
description = "RC transmitter support"
exports = {"notify": notify, "notify_lost": notify_lost}
schema = {}
tags = "experimental"
