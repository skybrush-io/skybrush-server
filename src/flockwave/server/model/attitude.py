__all__ = ("Attitude",)


class Attitude:
    """Class representing the attitude/orientation of a single UAV using the
    standard roll, pitch and yaw angles."""

    _roll: float
    _pitch: float
    _yaw: float

    @classmethod
    def from_json(cls, data):
        """Creates an Attitude from its JSON representation."""

        return cls(
            roll=data[0] * 1e-1,
            pitch=data[1] * 1e-1,
            yaw=data[2] * 1e-1,
        )

    def __init__(self, roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0):
        """Constructor.

        Args:
            roll: the roll angle in [deg]
            pitch: the pitch angle in [deg]
            yaw: the yaw angle in [deg]
        """
        self._roll, self._pitch, self._yaw = 0.0, 0.0, 0.0
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw

    @property
    def roll(self) -> float:
        """Returns the roll angle of the UAV in [deg]."""
        return self._roll

    @roll.setter
    def roll(self, value: float) -> None:
        self._roll = float(value)

    @property
    def pitch(self) -> float:
        """Returns the pitch angle of the UAV in [deg]."""
        return self._pitch

    @pitch.setter
    def pitch(self, value: float) -> None:
        self._pitch = float(value)

    @property
    def yaw(self) -> float:
        """Returns the yaw angle of the UAV in [deg]."""
        return self._yaw

    @yaw.setter
    def yaw(self, value: float) -> None:
        self._yaw = float(value)

    @property
    def json(self):
        roll = int(round(self.roll * 10)) % 3600
        if roll >= 1800:
            roll -= 3600
        pitch = int(round(self.pitch * 10)) % 3600
        if pitch >= 1800:
            pitch -= 3600
        yaw = int(round(self.yaw * 10)) % 3600

        return [roll, pitch, yaw]

    def update_from(self, other):
        self._roll = other._roll
        self._pitch = other._pitch
        self._yaw = other._yaw
