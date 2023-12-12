from dataclasses import dataclass
from math import ceil, inf
from operator import attrgetter
from typing import Iterable, Optional, Sequence, TypeVar, Union

__all__ = (
    "RelativeYawSetpoint",
    "YawSetpoint",
    "YawSetpointList",
)


C = TypeVar("C", bound="YawSetpointList")


@dataclass
class YawSetpoint:
    """The simplest representation of a yaw setpoint."""

    time: float
    """The timestamp associated to the yaw setpoint, in seconds."""

    yaw: float
    """The yaw angle associated to the yaw setpoint, in degrees."""


@dataclass
class RelativeYawSetpoint:
    """The simplest representation of a relative yaw setpoint."""

    duration: float
    """The duration associated to the relative yaw setpoint, in seconds."""

    yaw_change: float
    """The yaw change associated to the relative yaw setpoint, in degrees."""


class YawSetpointList:
    """Simplest representation of a causal yaw setpoint list in time.

    Setpoints are assumed to be linear, i.e. yaw rate is constant
    between setpoints.
    """

    def __init__(
        self,
        setpoints: Sequence[Union[YawSetpoint, tuple[float, float]]] = [],
        auto_yaw: bool = False,
        auto_yaw_offset: float = 0,
    ):
        if auto_yaw and setpoints:
            raise ValueError(
                "Setpoints cannot be used with auto yaw in yaw control block"
            )

        setpoints = [
            p if isinstance(p, YawSetpoint) else YawSetpoint(*p) for p in setpoints
        ]

        self.setpoints = sorted(setpoints, key=attrgetter("time"))
        self.auto_yaw = auto_yaw
        self.auto_yaw_offset = auto_yaw_offset

    @classmethod
    def from_json(cls, data: dict):
        """Constructs a yaw setpoint list from its JSON representation typically
        used in show specifications.

        Args:
            data: the JSON dictionary to import

        Returns:
            the yaw setpoint list created from its JSON representation

        Raises:
            RuntimeError on parsing errors
        """

        version: Optional[int] = data.get("version")

        if version is None:
            raise ValueError("Version is required")

        if version != 1:
            raise ValueError("Only version 1 of yaw control is supported")

        # ----------------------------------------------------------------------

        auto_yaw: bool = data.get("autoYaw", False)

        if isinstance(auto_yaw, (float, int)):
            auto_yaw = bool(auto_yaw)
        if not isinstance(auto_yaw, bool):
            raise ValueError("Yaw control's auto yaw value must be a boolean")

        # ----------------------------------------------------------------------

        auto_yaw_offset: float = data.get("autoYawOffset", 0)

        if isinstance(auto_yaw_offset, int):
            auto_yaw_offset = float(auto_yaw_offset)
        if not isinstance(auto_yaw_offset, float):
            raise ValueError("Yaw control's auto yaw offset must be a number")

        # ----------------------------------------------------------------------

        setpoints: list[tuple[float, float]] = data.get("setpoints", [])

        # ----------------------------------------------------------------------

        return cls(
            setpoints=setpoints, auto_yaw=auto_yaw, auto_yaw_offset=auto_yaw_offset
        )

    def iter_setpoints_as_relative(
        self, max_duration: float = inf, max_yaw_change: float = inf
    ) -> Iterable[RelativeYawSetpoint]:
        if self.setpoints:
            last_yaw = self.yaw_offset
            last_time = min(0, self.setpoints[0].time)
            for setpoint in self.setpoints:
                duration = setpoint.time - last_time
                yaw_change = setpoint.yaw - last_yaw
                # We need to split too long or too "turny" setpoints
                num_splits = max(
                    ceil(duration / max_duration) - 1,
                    ceil(abs(yaw_change) / max_yaw_change) - 1,
                    0,
                )
                ratio = 1 / (num_splits + 1)
                while num_splits >= 0:
                    yield RelativeYawSetpoint(
                        duration * ratio,
                        yaw_change * ratio,
                    )
                    num_splits -= 1
                # store last setpoint attributes
                last_time = setpoint.time
                last_yaw = setpoint.yaw

    @property
    def yaw_offset(self) -> float:
        """Returns the yaw offset associated to the yaw setpoint list."""
        if self.auto_yaw:
            return self.auto_yaw_offset

        if not self.setpoints:
            return 0

        return self.setpoints[0].yaw
