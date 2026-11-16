__all__ = ("WindEstimate",)


class WindEstimate:
    """Class representing the horizontal wind speed and direction as estimated
    by a single UAV.
    """

    _speed: float
    _direction: float

    @classmethod
    def from_json(cls, data):
        """Creates a WindEstimate from its JSON representation."""

        return cls(speed=data[0] * 1e-3, direction=data[1] * 1e-1)

    def __init__(self, speed: float = 0.0, direction: float = 0.0):
        """Constructor.

        Args:
            speed: the horizontal wind speed in [m/s]
            direction: the direction where the wind blows from, in [deg],
                clockwise from North
        """
        self._speed, self._direction = 0.0, 0.0
        self.speed = speed
        self.direction = direction

    @property
    def speed(self) -> float:
        """Returns the horizontal wind speed in [m/s]."""
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = float(value)

    @property
    def direction(self) -> float:
        """Returns the direction where the wind blows from, in [deg], clockwise
        from North.
        """
        return self._direction

    @direction.setter
    def direction(self, value: float) -> None:
        self._direction = float(value)

    @property
    def json(self):
        speed = max(int(round(self.speed * 1000)), 0)
        direction = int(round(self.direction * 10)) % 3600
        return [speed, direction]

    def update_from(self, other):
        self._speed = other._speed
        self._direction = other._direction
