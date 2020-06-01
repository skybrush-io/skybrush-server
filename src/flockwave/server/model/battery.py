from typing import Optional

__all__ = ("BatteryInfo",)


class BatteryInfo:
    """Class representing the battery information of a single UAV."""

    def __init__(self):
        self._voltage = None
        self._percentage = None

    @property
    def percentage(self) -> Optional[int]:
        return self._percentage

    @percentage.setter
    def percentage(self, value: Optional[int]) -> None:
        self._percentage = int(value) if value is not None else None

    @property
    def voltage(self) -> Optional[float]:
        return self._voltage

    @voltage.setter
    def voltage(self, value: Optional[float]) -> None:
        self._voltage = float(value) if value is not None else None

    @property
    def json(self):
        if self.voltage is None:
            return [0.0]
        elif self.percentage is None:
            return [int(round(self.voltage * 10))]
        else:
            return [int(round(self.voltage * 10)), self.percentage]

    @json.setter
    def json(self, value):
        if len(value) == 0:
            self._voltage = self._percentage = None
        else:
            self._voltage = value[0] / 10
            self._percentage = None if len(value) < 2 else int(value[1])

    def update_from(self, other):
        self._voltage = other._voltage
        self._percentage = other._percentage
