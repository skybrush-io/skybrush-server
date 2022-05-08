"""Helper functions to place N virtual drones in some predefined takeoff
grid.

Placement functions work in a flat Earth coordinate system where the origin
is at (0, 0, 0). The functions take the number of drones as the first
argument; additional keyword arguments may be provided for customizing the
shape provided by the placement function. The functions return the coordinates
in the flat Earth coordinate system. The caller must then map these into
GPS coordinates using a FlatEarthToGPSCoordinateTransformation_ object.
"""

from functools import partial
from math import cos, floor, pi, radians, sin
from typing import Callable, List, Optional, Tuple, Union

from flockwave.gps.vectors import Vector3D


__all__ = ("place_drones", "register")


_registry = {}


def register(name: str, func: Optional[Callable] = None):
    """Registers the given drone placement function with the given name.
    When the function is omitted, returns a decorator that can be applied to
    a function to register it.

    Parameters:
        name: the name with which the drone placement function should be
            registered
        func: the function to register

    Returns:
        the function itself if the function is specified; an appropriate
        decorator if the function is not specified
    """
    if func is None:
        return partial(register, name)

    if name in _registry:
        raise RuntimeError(f"{name} is already registered")

    _registry[name] = func
    return func


def place_drones(n: int, *, type: str, **kwds):
    """Generic drone placement function that takes the number of drones to
    place and a placement type constant (e.g., ``circle``, ``line``, ``grid``
    and similar). Invokes the appropriate drone placement function from this
    module and returns the result.

    Parameters:
        n: the number of drones to place
        type: name of the placement function; e.g., ``circle``, ``line``,
            ``grid`` and similar.

    Returns:
        the list of calculated flat Earth coordinates
    """
    try:
        func = _registry[type]
    except KeyError:
        raise RuntimeError(f"no such takeoff area shape: {type}")
    return func(n, **kwds)


@register("explicit")
def place_drones_explicitly(n: int, *, coordinates: List[Vector3D]) -> List[Vector3D]:
    """Returns coordinates to place the given number of drones with explicit
    flat Earth coordinates.

    Parameters:
        n: the number of drones to place; must be less than or equal to the
            length of the coordinate list
        coordinates: the list of coordinates; each item must be another list or
            tuple of X-Y or X-Y-Z coordinates.

    Returns:
        the list of flat Earth coordinates that were passed in
    """
    if len(coordinates) < n:
        raise RuntimeError(f"coordinate list must contain at least {n} items")

    result = []
    for item in coordinates[:n]:
        if len(item) < 2 or len(item) > 3:
            raise ValueError(
                "invalid coordinate list; we need two or three coordinates"
            )

        if len(item) == 2:
            x, y = item
            z = 0
        else:
            x, y, z = item

        result.append(Vector3D(x=float(x), y=float(y), z=float(z)))

    return result


@register("circle")
def place_drones_on_circle(
    n: int, *, radius: Optional[float] = None, min_distance: float = 5
) -> List[Vector3D]:
    """Returns coordinates to place the given number of drones in a circle.
    The circle will be centered at the origin. The first drone will be placed
    at degree zero (heading North, on the X axis).

    Parameters:
        n: the number of drones to place
        radius: the radius of the circle
        min_distance: the minimum distance between drones in the circle, along
            the circumference of the circle. Used only when the radius is not
            given explicitly.

    Returns:
        the list of calculated flat Earth coordinates
    """
    if radius is not None:
        radius = float(radius)

    if min_distance <= 0:
        raise ValueError("minimum distance cannot be negative")

    if radius is None:
        # 2 * r * pi = n * min_distance
        # r = n * min_distance / 2 / pi
        radius = n * min_distance / 2 / pi

    radius = float(radius)
    if radius <= 0 and n > 0:
        raise ValueError("radius must be positive")

    angles = [radians(i * 360 / n) for i in range(n)]
    return [Vector3D(x=radius * cos(angle), y=radius * sin(angle)) for angle in angles]


@register("grid")
def place_drones_on_grid(
    n: int,
    *,
    spacing: Union[float, Tuple[float, float]] = 5,
    rows: Optional[float] = None,
) -> List[Vector3D]:
    """Returns coordinates to place the given number of drones in a regular
    grid.

    The spacing argument specifies the distance between neighboring drones
    along the X and the Y axes, respectively. When a single number is used for
    the spacing, it is assumed that the spacing along both axes is the same.
    You may flip the axes by specifying negative spacing.

    The first drone of the grid will be at the origin. When a row count is
    given, the first N drones will be placed in the first column (rows 1,
    2, ..., N), the next N drones will be placed in the second column and so on.
    When a row count is not given, the number of rows will be set to the
    square root of the number of drones, rounded down.

    Parameters:
        n: the number of drones to place
        spacing: distance between drones along the axes, in meters
        rows: the desired number of rows; ``None`` means to choose automatically
            by fitting the drones (roughly) in a square

    Returns:
        the list of calculated flat Earth coordinates
    """
    if n <= 0:
        return []

    n = int(n)

    if not hasattr(spacing, "__iter__"):
        spacing = spacing, spacing

    if rows is None:
        rows = int(floor(n**0.5))

    result = []
    x, y = 0, 0
    xs, ys = spacing

    for i in range(n):
        y, x = divmod(i, rows)
        result.append(Vector3D(x=x * xs, y=y * ys))

    return result


@register("line")
def place_drones_on_line(n: int, *, spacing: float = 5) -> List[Vector3D]:
    """Returns coordinates to place the given number of drones in a straight
    line along the Y axis.

    The spacing argument specifies the distance between neighboring drones.

    Parameters:
        n: the number of drones to place
        spacing: distance between drones along the Y axis, in meters

    Returns:
        the list of calculated flat Earth coordinates
    """
    return place_drones_on_grid(n, spacing=spacing, rows=1)
