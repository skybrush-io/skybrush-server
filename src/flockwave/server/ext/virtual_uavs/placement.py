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
from math import cos, pi, radians, sin
from typing import Callable, List, Optional

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
