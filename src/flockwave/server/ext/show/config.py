"""Configuration object for the drone show extension."""

from __future__ import annotations

from blinker import Signal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TypeVar

__all__ = ("DroneShowConfiguration", "StartMethod")


class StartMethod(Enum):
    """Enumeration holding the possible start methods for a drone show."""

    #: Show starts only with RC
    RC = "rc"

    #: Show starts automatically based on GPS time or MIDI timecode
    AUTO = "auto"


class LightEffectType(Enum):
    """Enumeration holding the type of light effects that could be configured
    on the drones.
    """

    #: GCS is not controlling the LED lights on the drones
    OFF = "off"

    #: GCS is asking the drones to use a solid LED light
    SOLID = "solid"


C = TypeVar("C", bound="DroneShowConfiguration")


class DroneShowConfiguration:
    """Main configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    authorized_to_start: bool
    start_method: StartMethod
    start_time: Optional[float]
    uav_ids: List[Optional[str]]

    def __init__(self):
        """Constructor."""
        self.authorized_to_start = False
        self.start_time = None
        self.start_method = StartMethod.RC
        self.uav_ids = []

    def clone(self: C) -> C:
        """Makes an exact shallow copy of the configuration object."""
        result = self.__class__()
        result.update_from_json(self.json)
        return result

    @property
    def json(self) -> Dict[str, Any]:
        """Returns the JSON representation of the configuration object."""
        return {
            "start": {
                "authorized": bool(self.authorized_to_start),
                "time": self.start_time,
                "method": str(self.start_method.value),
                "uavIds": self.uav_ids,
            }
        }

    def update_from_json(self, obj: Dict[str, Any]) -> None:
        """Updates the configuration object from its JSON representation."""
        changed = False

        start_conditions = obj.get("start")
        if start_conditions:
            if "authorized" in start_conditions:
                # This is intentional; in order to be on the safe side, we only
                # accept True for authorization, not any other truthy value
                self.authorized_to_start = start_conditions["authorized"] is True
                changed = True

            if "time" in start_conditions:
                start_time = start_conditions["time"]
                if start_time is None:
                    self.start_time = None
                    changed = True
                elif isinstance(start_time, (int, float)):
                    self.start_time = float(start_time)
                    changed = True

            if "method" in start_conditions:
                self.start_method = StartMethod(start_conditions["method"])
                changed = True

            if "uavIds" in start_conditions:
                uav_ids = start_conditions["uavIds"]
                if isinstance(uav_ids, list) and all(
                    item is None or isinstance(item, str) for item in uav_ids
                ):
                    self.uav_ids = uav_ids
                    changed = True

        if changed:
            self.updated.send(self)


#: Type alias for an RGB color
RGBColor = Tuple[int, int, int]


class LightConfiguration:
    """LED light related configuration object for the drone show extension."""

    updated = Signal(doc="Signal emitted when the configuration is updated")

    color: RGBColor
    effect: LightEffectType

    @classmethod
    def create_solid_color(cls, color: RGBColor) -> "LightConfiguration":
        result = cls()
        result.color = tuple(color)  # type: ignore
        result.effect = LightEffectType.SOLID
        return result

    @classmethod
    def turn_off(cls) -> "LightConfiguration":
        return cls()

    def __init__(self):
        """Constructor."""
        self.color = (0, 0, 0)
        self.effect = LightEffectType.OFF

    def clone(self) -> "LightConfiguration":
        """Makes an exact shallow copy of the configuration object."""
        result = self.__class__()
        result.update_from_json(self.json)
        return result

    @property
    def json(self):
        """Returns the JSON representation of the configuration object."""
        return {"color": list(self.color), "effect": str(self.effect.value)}

    def update_from_json(self, obj):
        """Updates the configuration object from its JSON representation."""
        changed = False

        color = obj.get("color")
        if color:
            if (
                isinstance(color, (list, tuple))
                and len(color) >= 3
                and all(isinstance(x, (int, float)) for x in color)
            ):
                self.color = tuple(int(x) for x in color)  # type: ignore
                # Send a signal even if the color stayed the same; maybe the
                # user sent the same configuration again because some of the
                # drones in the show haven't received the previous request
                changed = True

        effect = obj.get("effect")
        if effect:
            # Send a signal even if the effect stayed the same; maybe the
            # user sent the same configuration again because some of the
            # drones in the show haven't received the previous request
            self.effect = LightEffectType(effect)
            changed = True

        if changed:
            self.updated.send(self)
