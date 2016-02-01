"""Classes representing coordinate, velocity, attitude vectors and
similar things.
"""

from __future__ import absolute_import, division

from flockwave.spec.schema import get_complex_object_schema
from math import cos, pi, sin, sqrt
from .constants import PI_OVER_180, WGS84
from .metamagic import ModelMeta


__all__ = ("GPSCoordinate", "FlatEarthCoordinate",
           "FlatEarthToGPSCoordinateTransformation")


class GPSCoordinate(object):
    """Class representing a GPS coordinate."""

    __metaclass__ = ModelMeta

    class __meta__:
        schema = get_complex_object_schema("gpsCoordinate")

    # TODO: float() casts should be included in the lat/lon/altRel/altMSL
    # setters

    def __init__(self, lat=0.0, lon=0.0, altRel=None, altMSL=None):
        """Constructor.

        Parameters:
            lat (float): the latitude
            lon (float): the longitude
            altRel (float or None): the relative altitude; ``None`` means
                unspecified
            altMSL (float or None): the altitude above mean sea level;
                ``None`` means unspecified
        """
        self.lat = float(lat)
        self.lon = float(lon)
        if altRel is not None:
            self.altRel = float(altRel)
        if altMSL is not None:
            self.altMSL = float(altMSL)

    def update(self, lat=None, lon=None, altRel=None, altMSL=None):
        """Updates the coordinates of this object.

        Parameters:
            lat (float or None): the new latitude; ``None`` means to
                leave the current value intact.
            lon (float or None): the new longitude; ``None`` means to
                leave the current value intact.
            altRel (float or None): the new relative altitude; ``None``
                means to leave the current value intact.
            altMSL (float or None): the new altitde above mean sea level;
                `None`` means to leave the current value intact.
        """
        if lat is not None:
            self.lat = lat
        if lon is not None:
            self.lon = lon
        if altRel is not None:
            self.altRel = altRel
        if altMSL is not None:
            self.altMSL = altMSL

    def update_from(self, other):
        """Updates the coordinates of this object from another instance
        of GPSCoordinate_.

        Parameters:
            other (GPSCoordinate): the other object to copy the values from.
        """
        self.update(**other.json)


class FlatEarthCoordinate(object):
    """Class representing a coordinate given in flat Earth coordinates."""

    def __init__(self, x=0.0, y=0.0, altRel=None, altMSL=None):
        """Constructor.

        Parameters:
            x (float): the X coordinate
            y (float): the Y coordinate
            altRel (float or None): the relative altitude; ``None`` means
                unspecified
            altMSL (float or None): the altitude above mean sea level;
                ``None`` means unspecified
        """
        self._x, self._y, self._altRel, self._altMSL = 0.0, 0.0, None, None
        self.x = x
        self.y = y
        self.altRel = altRel
        self.altMSL = altMSL

    @property
    def x(self):
        """The X coordinate."""
        return self._x

    @x.setter
    def x(self, value):
        self._x = float(value)

    @property
    def y(self):
        """The Y coordinate."""
        return self._y

    @y.setter
    def y(self, value):
        self._y = float(value)

    @property
    def altMSL(self):
        """The altitude above mean sea level, if known."""
        return self._altMSL

    @altMSL.setter
    def altMSL(self, value):
        self._altMSL = float(value) if value is not None else None

    @property
    def altRel(self):
        """The relative altitude, if known."""
        return self._altRel

    @altRel.setter
    def altRel(self, value):
        self._altRel = float(value) if value is not None else None

    def update(self, x=None, y=None, altRel=None, altMSL=None):
        """Updates the coordinates of this object.

        Parameters:
            x (float or None): the new X coordinate; ``None`` means to
                leave the current value intact.
            y (float or None): the new Y coordinate; ``None`` means to
                leave the current value intact.
            altRel (float or None): the new relative altitude; ``None``
                means to leave the current value intact.
            altMSL (float or None): the new altitde above mean sea level;
                `None`` means to leave the current value intact.
        """
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if altRel is not None:
            self.altRel = altRel
        if altMSL is not None:
            self.altMSL = altMSL


class FlatEarthToGPSCoordinateTransformation(object):
    """Transformation that converts flat Earth coordinates to GPS
    coordinates and vice versa.
    """

    def __init__(self, origin=None):
        """Constructor.

        Parameters:
            origin (GPSCoordinate): origin of the flat Earth coordinate
                system, in GPS coordinates. Altitude component is ignored.
                The coordinate will be copied.
        """
        self._origin_lat = None
        self._origin_lon = None
        self.origin = origin if origin is not None else GPSCoordinate()

    @property
    def origin(self):
        """The origin of the transformation, in GPS coordinates. The
        property uses a copy so you can safely modify the value returned
        by the getter without affecting the transformation.
        """
        return GPSCoordinate(lat=self._origin_lat, lon=self._origin_lon)

    @origin.setter
    def origin(self, value):
        self._origin_lat = float(value.lat)
        self._origin_lon = float(value.lon)
        self._recalculate()

    def _recalculate(self):
        """Recalculates some cached values that are re-used across different
        transformations.
        """
        self._pi_over_180 = pi / 180

        earth_radius = WGS84.EQUATORIAL_RADIUS_IN_METERS
        eccentricity_sq = WGS84.ECCENTRICITY_SQUARED

        origin_lat_in_radians = self._origin_lat * self._pi_over_180
        self._cos_origin_lat_in_radians = cos(origin_lat_in_radians)

        x = (1 - eccentricity_sq * (sin(origin_lat_in_radians) ** 2))
        self._r1 = earth_radius * (1 - eccentricity_sq) / (x ** 1.5)
        self._r2 = earth_radius / sqrt(x)

    def to_flat_earth(self, coord):
        """Converts the given GPS coordinates to flat Earth coordinates.

        Parameters:
            coord (GPSCoordinate): the coordinate to convert

        Returns:
            FlatEarthCoordinate: the converted coordinate
        """
        raise NotImplementedError

    def to_gps(self, coord):
        """Converts the given flat Earth coordinates to GPS coordinates.

        Parameters:
            coord (FlatEarthCoordinate): the coordinate to convert

        Returns:
            GPSCoordinate: the converted coordinate
        """
        lat_in_radians = coord.x / self._r1
        lon_in_radians = coord.y / self._r2 / self._cos_origin_lat_in_radians
        return GPSCoordinate(
            lat=lat_in_radians / PI_OVER_180 + self._origin_lat,
            lon=lon_in_radians / PI_OVER_180 + self._origin_lon,
            altRel=coord.altRel,
            altMSL=coord.altMSL
        )
