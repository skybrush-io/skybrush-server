from enum import IntEnum
from typing import Any, Optional

__all__ = ("ControllerType",)


class ControllerType(IntEnum):
    """Enum representing the controller types used in the Crazyflie ecosystem."""

    AUTO_SELECT = 0
    PID = 1
    MELLINGER = 2
    INDI = 3
    BRESCIANINI = 4

    @classmethod
    def from_json(cls, value: Any) -> Optional["ControllerType"]:
        if value is None:
            return None

        if isinstance(value, str):
            value = value.lower()
            if value == "auto" or value == "autoselect":
                return cls.AUTO_SELECT
            elif value == "pid":
                return cls.PID
            elif value == "mellinger":
                return cls.MELLINGER
            elif value == "indi":
                return cls.INDI
            elif value == "brescianini":
                return cls.BRESCIANINI

        return cls(value)
