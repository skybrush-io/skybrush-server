from dataclasses import dataclass
from operator import attrgetter
from typing import Sequence, TypeVar, Union

__all__ = (
    "YawSetpoint",
    "YawSetpointList",
)


C = TypeVar("C", bound="YawSetpointList")


@dataclass
class YawSetpoint:
    """The simplest representation of a yaw setpoint."""

    time: float
    """The timestamp associated to the yaw setpoint, in seconds."""

    angle: float
    """The yaw angle associated to the yaw setpoint, in degrees."""


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
        """

        version: int = data.get("version", 0)

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

    @property
    def yaw_offset(self) -> float:
        """Returns the yaw offset associated to self."""
        if self.auto_yaw:
            return self.auto_yaw_offset

        if not self.setpoints:
            return 0

        return self.setpoints[0].angle
