from typing import Optional, List, Sequence, Tuple

__all__ = ("BoundingBoxCalculator", "Point", "encode_variable_length_integer")

#: Type specification for a single point in a trajectory
Point = Tuple[float, float, float]


class BoundingBoxCalculator:
    """Class that iteratively calculates the axis-aligned bounding box of a
    set of points.
    """

    _max: Optional[List[float]]
    _min: Optional[List[float]]

    def __init__(self, dim: int = 3):
        """Constructor.

        Parameters:
            dim: the dimensions of the bounding box
        """
        self._dim = int(dim)
        self._min, self._max = None, None

    @property
    def is_empty(self) -> bool:
        """Returns whether the bounding box is empty (has no points)."""
        return self._min is None

    def add(self, point: Sequence[float]) -> None:
        """Adds a new point to the set of points."""
        if self.is_empty:
            self._min = list(point)
            self._max = list(point)
        else:
            assert self._min is not None and self._max is not None
            for i in range(self._dim):
                self._min[i] = min(self._min[i], point[i])
                self._max[i] = max(self._max[i], point[i])

    def get_corners(self) -> Tuple[Sequence[float], Sequence[float]]:
        """Returns the opposite corners of the bounding box.

        Raises:
            ValueError: if no points were added to the bounding box yet
        """
        if self.is_empty:
            raise ValueError("the bounding box is empty")
        else:
            assert self._min is not None and self._max is not None
            return tuple(self._min), tuple(self._max)  # type: ignore

    def pad(self, amount: float) -> None:
        """Pads the bounding box on each side with the given padding.

        No changes are made when the bounding box has no points yet.
        """
        if amount < 0:
            raise ValueError("padding must be non-negative")

        if amount > 0 and not self.is_empty:
            assert self._min is not None and self._max is not None
            for i in range(self._dim):
                self._min[i] -= amount
                self._max[i] += amount


def encode_variable_length_integer(value: int) -> bytes:
    """Encodes a non-negative integer into variable-length format.

    Variable-length integers are stored as follows. If the number is less than
    128, it is encoded into a single byte. Otherwise, the lowermost 7 bits are
    encoded into a single byte, the MSB of the byte is set to 1, and the
    process continues with the integer shifted to the right by 7 bits.
    """
    if value < 0:
        raise ValueError("cannot encode negative integers as variable length")

    data = []
    while True:
        if value >= 0x80:
            data.append((value & 0x7F) | 0x80)
            value >>= 7
        else:
            data.append(value)
            return bytes(data)
