from typing import Optional

__all__ = ("BatteryInfo",)


class BatteryInfo:
    """Class representing the battery information of a single UAV."""

    def __init__(self):
        self._charging = False
        self._voltage = None
        self._percentage = None

    @property
    def charging(self) -> bool:
        return self._charging

    @charging.setter
    def charging(self, value: bool) -> None:
        self._charging = bool(value)

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
            result = [0.0]
        elif self.percentage is None:
            result = [int(round(self.voltage * 10))]
        else:
            result = [int(round(self.voltage * 10)), self.percentage]
        if self._charging:
            while len(result) < 2:
                result.append(None)
            result.append(True)
        return result

    @json.setter
    def json(self, value):
        length = len(value)
        if length == 0:
            self._voltage = self._percentage = None
            self._charging = False
        else:
            self._voltage = value[0] / 10
            if length < 2:
                self._percentage = None
                self._charging = False
            else:
                if value[1] is not None:
                    self._percentage = int(value[1])
                if length > 2:
                    self._charging = bool(value[2])

    def update_from(self, other):
        self._voltage = other._voltage
        self._percentage = other._percentage
        self._charging = other._charging
