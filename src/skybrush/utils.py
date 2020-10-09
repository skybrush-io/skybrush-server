from typing import Tuple

__all__ = ("BoundingBoxCalculator", "Point")

#: Type specification for a single point in a trajectory
Point = Tuple[float, float, float]


class BoundingBoxCalculator:
    """Class that iteratively calculates the axis-aligned bounding box of a
    set of points.
    """

    def __init__(self):
        """Constructor."""
        self._min, self._max = None, None

    def add(self, point: Point) -> None:
        """Adds a new point to the set of points."""
        if self._min is None:
            self._min = list(point)
            self._max = list(point)
        else:
            for i in range(3):
                self._min[i] = min(self._min[i], point[i])
                self._max[i] = max(self._max[i], point[i])

    def get_corners(self) -> Tuple[Point, Point]:
        """Returns the opposite corners of the bounding box.

        Raises:
            ValueError: if no points were added to the bounding box yet
        """
        if self._min is None:
            raise ValueError("the bounding box is empty")
        else:
            return tuple(self._min), tuple(self._max)
