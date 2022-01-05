"""Mapping objects that map DMX universes and channels to the LED lights of the
UAVs.
"""

from collections import defaultdict
from colour import Color
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Sequence


__all__ = ("DMXFixtureType", "DMXMapping", "DMXMappingEntry")


DMX_UNIVERSE_LENGTH = 512
"""Maximum number of DMX channels in a single universe."""


def _update_dimmer(color: Color, channels: List[int]) -> None:
    value = min(1, max(0, channels[0] / 255))
    color.rgb = (value, value, value)


def _update_rgb(color: Color, channels: List[int]) -> None:
    red = min(1, max(0, channels[0] / 255))
    green = min(1, max(0, channels[1] / 255))
    blue = min(1, max(0, channels[2] / 255))
    color.rgb = (red, green, blue)


def _update_rgb_dimmer(color: Color, channels: List[int]) -> None:
    red = min(1, max(0, channels[0] / 255))
    green = min(1, max(0, channels[1] / 255))
    blue = min(1, max(0, channels[2] / 255))
    value = min(1, max(0, channels[3] / 255))
    color.rgb = (red * value, green * value, blue * value)


def _update_dimmer_rgb(color: Color, channels: List[int]) -> None:
    value = min(1, max(0, channels[0] / 255))
    red = min(1, max(0, channels[1] / 255))
    green = min(1, max(0, channels[2] / 255))
    blue = min(1, max(0, channels[3] / 255))
    color.rgb = (red * value, green * value, blue * value)


class DMXFixtureType(Enum):
    """DMX fixture types."""

    DIMMER = ("dimmer", 1, _update_dimmer)
    """A single dimmer, mapping a single DMX channel to shades of white in
    RGB space (0 = black, 255 = white)
    """

    RGB = ("rgb", 3, _update_rgb)
    """RGB fixture, mapping three consecutive DMX channels to a single color
    in RGB space.
    """

    RGB_DIMMER = ("rgb_dimmer", 4, _update_rgb_dimmer)
    """RGB fixture with a separate dimmer channel. Four consecutive DMX channels
    are mapped to a single color in RGB space; channels 1-3 are the R, G and
    B components, channel 4 is the brightness.
    """

    DIMMER_RGB = ("dimmer_rgb", 4, _update_dimmer_rgb)
    """RGB fixture with a separate dimmer channel. Four consecutive DMX channels
    are mapped to a single color in RGB space; channel 1 is the brightness, and
    channels 2-4 are the R, G and B components.
    """

    num_channels: int
    _updater: Callable[[Color, List[int]], None]

    def __new__(
        cls, value: str, num_channels: int, updater: Callable[[Color, List[int]], None]
    ):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.num_channels = num_channels
        obj._updater = updater
        return obj

    def update_color_from_channels(self, color: Color, channels: List[int]) -> None:
        self._updater(color, channels)


@dataclass(frozen=True)
class DMXMappingEntry:
    """A single immutable entry in a DMX mapping object."""

    universe: int = 0
    """The universe that this entry belongs to."""

    start_channel: int = 0
    """The first DMX channel that this mapping covers."""

    fixture_model: DMXFixtureType = DMXFixtureType.RGB
    """The fixture model used in this mapping; typically RGB."""

    num_fixtures: int = 1
    """The number of fixtures in this DMX block. A single fixture is mapped
    to a single drone.
    """

    uav_id_getter: Callable[[int], str] = "{0:02}".format
    """A function that receives a zero-based fixture index in this block and
    returns the ID of the UAV that the fixture belongs to.
    """

    @property
    def channel_range(self) -> Iterable[int]:
        """Returns an iterable that covers the DMX channel range corresponding
        to this mapping entry.
        """
        return range(self.start_channel, self.end_channel)

    @property
    def end_channel(self) -> int:
        """Returns the index of the last channel that is covered by this DMX
        mapping entry, plus one, clamped to the DMX universe size.
        """
        channels_per_fixture = self.fixture_model.num_channels
        num_channels = channels_per_fixture * self.num_fixtures
        return min(self.start_channel + num_channels, DMX_UNIVERSE_LENGTH)


_EMPTY_UNIVERSE: Sequence[Optional[DMXMappingEntry]] = (None,) * 512
"""Dummy object that represents a DMX universe where none of the channels are
mapped to UAVs.
"""


class DMXMapping:
    """A collection of DMX mapping entries."""

    _entries: List[DMXMappingEntry]
    """The entries in the mapping."""

    _entry_map: Dict[int, List[Optional[DMXMappingEntry]]]
    """Dictionary that maps DMX universe indices to lists of length 512 such
    that the i-th element of the list contains the DMX mapping entry that
    covers the given channel in the given universe.
    """

    def __init__(self, entries: Iterable[DMXMappingEntry] = ()):
        """Constructor."""
        self._entries = []
        self._entry_map = defaultdict(self._create_new_entry_in_map)
        for entry in entries:
            self.add(entry)

    def add(self, entry: DMXMappingEntry) -> None:
        """Adds a new entry to this mapping."""
        self._entries.append(entry)

        channel_to_entry = self._entry_map[entry.universe]
        for index in entry.channel_range:
            channel_to_entry[index] = entry

    def get_channel_map_for_universe(
        self, universe: int
    ) -> Sequence[Optional[DMXMappingEntry]]:
        return self._entry_map.get(universe, _EMPTY_UNIVERSE)

    @staticmethod
    def _create_new_entry_in_map() -> List[Optional[DMXMappingEntry]]:
        return [None] * DMX_UNIVERSE_LENGTH
