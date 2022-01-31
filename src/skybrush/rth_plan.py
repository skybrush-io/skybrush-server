from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import Dict, List, Optional, Sequence, Tuple

from .utils import BoundingBoxCalculator

__all__ = ("RTHAction", "RTHPlan", "RTHPlanEntry")


class RTHAction(Enum):
    LAND = "land"
    GO_TO_KEEPING_ALTITUDE_AND_LAND = "goTo"


@dataclass(frozen=True)
class RTHPlanEntry:
    """Single entry in a return-to-home plan of a single drone."""

    time: int
    """Timestamp of the entry. Must be an integer; floats are not allowed."""

    action: RTHAction
    """Action to perform when the return-to-home command is received at the
    given timestamp.
    """

    target: Tuple[float, ...] = ()
    """The target coordinate of the action, if applicable. Ignored if the
    action is ``RTHAction.LAND``
    """

    duration: int = 0
    """Duration of the action, in seconds, if applicable. Ignored if the action
    is ``RTHAction.LAND``. Must be an integer; floats are not allowed.
    """

    pre_delay: int = 0
    """Number of seconds to wait before starting the action. Must be an
    integer; floats are not allowed.
    """

    post_delay: int = 0
    """Number of seconds to wait after finishing the action, before proceeding
    with landing. Must be an integer; floats are not allowed.
    """

    @classmethod
    def from_json(cls, data: Dict):
        """Constructs an RTH plan entry from its JSON representation typically
        used in show specifications.
        """
        time = data.get("time")
        if time is None:
            raise ValueError("RTH plan entries must have timestamps")

        if isinstance(time, float) and time.is_integer():
            time = int(time)
        if not isinstance(time, int):
            raise ValueError("RTH plan entry timestamps must be integers")

        # ----------------------------------------------------------------------

        try:
            action = RTHAction(data.get("action"))
        except Exception:
            raise ValueError("invalid action found in RTH plan entry")

        if action is RTHAction.LAND:
            return cls(time=int(time), action=action)

        # ----------------------------------------------------------------------

        kwds = {"time": int(time), "action": action}

        target = tuple(data.get("target") or ())
        if len(target) != 2 or not all(
            isinstance(item, (int, float)) for item in target
        ):
            raise ValueError("targets in RTH plan entry must be pairs of numbers")
        kwds["target"] = target

        # ----------------------------------------------------------------------

        duration = data.get("duration")
        if duration is None:
            raise ValueError("RTH plan entries with targets must have durations")

        if isinstance(duration, float) and duration.is_integer():
            duration = int(duration)
        if not isinstance(duration, int):
            raise ValueError("RTH plan entry durations must be integers")

        kwds["duration"] = duration

        # ----------------------------------------------------------------------

        pre_delay = data.get("preDelay") or 0
        if isinstance(pre_delay, float) and pre_delay.is_integer():
            pre_delay = int(pre_delay)
        if not isinstance(pre_delay, int):
            raise ValueError("RTH plan entry pre-delays must be integers")
        if pre_delay > 0:
            kwds["pre_delay"] = pre_delay

        # ----------------------------------------------------------------------

        post_delay = data.get("postDelay") or 0
        if isinstance(post_delay, float) and post_delay.is_integer():
            post_delay = int(post_delay)
        if not isinstance(post_delay, int):
            raise ValueError("RTH plan entry post-delays must be integers")
        if post_delay > 0:
            kwds["post_delay"] = post_delay

        return cls(**kwds)

    @property
    def has_pre_delay(self) -> bool:
        """Returns whether the entry has a (positive) pre-delay."""
        return self.pre_delay > 0

    @property
    def has_post_delay(self) -> bool:
        """Returns whether the entry has a (positive) post-delay."""
        return self.post_delay > 0

    @property
    def has_target(self) -> bool:
        """Returns whether the action of this entry impliese that the entry
        has a target coordinate to consider.
        """
        return self.action is RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND

    def is_same_as_except_timestamp(self, other: "RTHPlanEntry") -> bool:
        """Compares this entry with another one and returns whether they are
        the same, _except_ their timestamps.
        """
        return (
            self.action == other.action
            and self.target == other.target
            and self.duration == other.duration
            and self.pre_delay == other.pre_delay
            and self.post_delay == other.post_delay
        )

    def to_json(self) -> Dict:
        """Returns a JSON representation of the RTH plan entry."""
        result = {"time": self.time, "action": self.action.value}

        if self.has_target:
            result["target"] = self.target
            result["duration"] = self.duration

            if self.has_pre_delay:
                result["preDelay"] = self.pre_delay

            if self.has_post_delay:
                result["postDelay"] = self.post_delay

        return result


class RTHPlan(Sequence[RTHPlanEntry]):
    """Object representing the return-to-home plan of a single drone during
    a mission where it can be known in advance where the drone will be at
    a given timestamp.
    """

    _entries: List[RTHPlanEntry]

    @classmethod
    def from_json(cls, data: Dict):
        """Constructs an RTH plan from its JSON representation typically used
        in show specifications.
        """
        plan = cls()

        version = data.get("version")
        if version != 1:
            raise RuntimeError("only version 1 RTH plans are supported")

        entries = data.get("entries")
        if entries is None or not hasattr(entries, "__iter__"):
            raise RuntimeError("entries not found in RTH plan")

        for entry in entries:
            decoded_entry = RTHPlanEntry.from_json(entry)
            plan.add_entry(decoded_entry)

        return plan

    def __init__(self):
        """Constructor."""
        self._entries = []

    @property
    def bounding_box(self) -> Tuple[Sequence[float], Sequence[float]]:
        """The axis-aligned bounding box that encapsulates all target points
        of the RTH plan.
        """
        return self.get_padded_bounding_box()

    @property
    def is_empty(self):
        """Returns whether the plan is empty (i.e. has no entries)."""
        return len(self._entries) == 0

    @property
    def last_timestamp(self) -> Optional[int]:
        """Returns the timestamp of the last entry or ``None`` if the plan is
        empty.
        """
        return self._entries[-1].time if self._entries else None

    def add_entry(self, entry: RTHPlanEntry) -> None:
        """Adds a new entry to this RTH plan, validating that the timestamp of
        the entry is strictly larger than the timestamp of the last entry.
        """
        last = self.last_timestamp
        if last is not None and entry.time <= last:
            raise RuntimeError(
                "Cannot add entry to RTH plan; timestamp must be larger than "
                "the last entry of the RTH plan"
            )
        self._entries.append(entry)

    def clear(self) -> None:
        """Removes all entries from this RTH plan."""
        self._entries.clear()

    def get_padded_bounding_box(
        self, margin: float = 0
    ) -> Tuple[Sequence[float], Sequence[float]]:
        """Returns the coordinates of the opposite corners of the axis-aligned
        bounding box that contains all the target points of the RTH plan,
        optionally padded with the given margin.

        The first point will contain the minimum coordinates, the second will
        contain the maximum coordinates.

        Parameters:
            margin: the margin to apply on each side of the bounding box

        Raises:
            ValueError: if the margin is negative or if the RTH plan has no
                target point
        """
        bbox = BoundingBoxCalculator(dim=2)
        for entry in self._entries:
            if entry.has_target:
                bbox.add(entry.target)

        if margin > 0:
            bbox.pad(margin)

        return bbox.get_corners()

    def propose_scaling_factor(self) -> int:
        """Proposes a scaling factor to use in a Skybrush binary show file when
        storing the RTH plan.
        """
        try:
            mins, maxs = self.bounding_box
        except ValueError:
            # None of the entries in the RTH plan contain a target point
            return 1

        coords = []
        coords.extend(abs(x) for x in mins)
        coords.extend(abs(x) for x in maxs)
        extremum = ceil(max(coords) * 1000)

        # With scale=1, we can fit values from 0 to 32767 into the binary show
        # file, so we basically need to divide (extremum+1) by 32768 and round
        # up. This gives us scale = 1 for extrema in [0; 32767],
        # scale = 2 for extrema in [32768; 65535] and so on.
        return ceil((extremum + 1) / 32768)

    def to_json(self) -> Dict:
        """Returns a JSON representation of the RTH plan."""
        return {"version": 1, "entries": [entry.to_json() for entry in self._entries]}

    def __getitem__(self, index: int) -> RTHPlanEntry:
        return self._entries[index]

    def __len__(self) -> int:
        return len(self._entries)
