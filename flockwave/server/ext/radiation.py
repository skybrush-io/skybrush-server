"""Extension that allows one to place virtual "radiation sources" into
the world. Other extensions (such as ``fake_uav``) could then query the
locations of the radiation sources to provide UAVs with fake Geiger-Muller
counters.
"""

from __future__ import absolute_import, division

from flockwave.gps.vectors import ECEFToGPSCoordinateTransformation, \
    FlatEarthToGPSCoordinateTransformation, GPSCoordinate
from numpy.random import poisson

from .base import ExtensionBase


gps_to_ecef = ECEFToGPSCoordinateTransformation().to_ecef


class Source(object):
    """Object representing a single radiation source with a given location
    and intensity.
    """

    def __init__(self, lat, lon, intensity):
        """Constructor.

        Parameters:
            lat (float): the latitude of the radiation source
            lon (float): the longitude of the radiation source
            intensity (float): the intensity of the radiation source,
                expressed as the number of particles detected in one second
                by our Geiger-Muller counter at a distance of 1 meter from
                the source
        """
        self._flat_earth_trans = None
        self._location_gps = None
        self._location_ecef = None
        self._location_uses_relative_altitude = False

        self.location = GPSCoordinate(lat=lat, lon=lon, agl=0)
        self.intensity = max(float(intensity), 0.0)

    def intensity_at(self, point, point_in_ecef=None):
        """Returns the intensity of the radiation source at the given
        location.

        The format of the given location depends on whether the location of
        the source is specified with relative altitude or with altitude
        above the mean sea level. In case of relative altitudes, the
        intensity calculation needs a GPS coordinate with a relative
        altitude as well, and the distance will be calculated using a
        flat Earth approximation around the location of the source,
        assuming that the source and the given point uses the same reference
        point for the relative altitudes. If the source is specified with
        altitude above the mean sea level, the location should be given
        with ECEF coordinates as well as GPS coordinates, and the distance
        from the source will be calculated in the ECEF coordinate system.

        Parameters:
            point (GPSCoordinate): the point of the query, in GPS
                coordinates.
            point_in_ecef (Optional[ECEFCoordinate]): the point of the query,
                in ECEF coordinates. It can be ``None``, in which case it
                will be calculated if needed.

        Returns:
            float: the intensity of the radiation source at the given point,
                i.e. the expected number of particles that would be detected
                in one second at the given point from the radiation source
        """
        need_ecef = not self._location_uses_relative_altitude
        if need_ecef:
            if point_in_ecef is None:
                point_in_ecef = gps_to_ecef(point)
            dist_sq = self._location_ecef.distance(point_in_ecef) ** 2
        else:
            point_in_flat = self._flat_earth_trans.to_flat_earth(point)
            dist_sq = point_in_flat.x ** 2 + point_in_flat.y ** 2 + \
                point_in_flat.amsl.value ** 2
        return self.intensity / dist_sq

    @property
    def location(self):
        """The location of the radiation source as a GPSCoordinate_"""
        return self._location_gps

    @location.setter
    def location(self, value):
        self._location_gps = value
        self._location_uses_relative_altitude = value.amsl is None
        if self._location_uses_relative_altitude:
            self._location_ecef = None
            assert value.agl == 0
        else:
            self._location_ecef = gps_to_ecef(value)
        self._flat_earth_trans = FlatEarthToGPSCoordinateTransformation(
            origin=self._location_gps)


class RadiationExtension(ExtensionBase):
    """Extension that allows one to place virtual "radiation sources" into
    the world.
    """

    def __init__(self):
        """Constructor."""
        super(RadiationExtension, self).__init__()
        self._background_intensity = 0.0
        self._sources = []

    def add_source(self, lat, lon, intensity):
        """Adds a new radiation source.

        Parameters:
            lat (float): the latitude of the radiation source
            lon (float): the longitude of the radiation source
            intensity (float): the intensity of the radiation source,
                expressed as the number of particles detected in one second
                by our Geiger-Muller counter at a distance of 1 meter from
                the source
        """
        self._sources.append(Source(lat=lat, lon=lon, intensity=intensity))

    @property
    def background_intensity(self):
        """The intensity of the background radiation."""
        return self._background_intensity

    @background_intensity.setter
    def background_intensity(self, value):
        if value is None:
            value = 0.0
        self._background_intensity = max(float(value), 0.0)

    def configure(self, configuration):
        """Configures the extension.

        The configuration object supports the following keys:

        ``background``
            The intensity of the background radiation.

        ``sources``
            List containing the radiation sources. Each item in the list
            must be a dictionary containing keys named ``lat`` (latitude),
            ``lon`` (longitude) and ``intensity``. Radiation sources are
            assumed to emit particles according to a Poisson distribution
            with the given intensity, decaying proportionally to the square
            of the distance. Distances are calculated in the ECEF coordinate
            system.
        """
        self.background_intensity = configuration.get("background", 0)
        for source in configuration.get("sources", []):
            self.add_source(**source)

    def exports(self):
        """Returns the functions exported by the extension."""
        return {
            "measure_at": self._measure_at
        }

    def _measure_at(self, point, seconds=1):
        """Conducts a fake measurement of radiation at the given point
        for the given amount of time.

        Parameters:
            point (GPSCoordinate): the point to measure the radiation
                intensity at
            seconds (float): the total number of seconds

        Returns
            float: the observed number of particles detected at the given
                location in the given number of seconds
        """
        assert seconds >= 0
        if seconds == 0:
            return 0.0
        else:
            return poisson(self._total_intensity_at(point) * seconds)

    def _total_intensity_at(self, point):
        """Returns the total radiation intensity at the given point.

        The total radiation intensity is the expected number of particles
        that would be detected at the given position from the sources and
        the background radiation in one second.

        Parameters:
            point (GPSCoordinate): the point to measure the radiation
                intensity at

        Returns
            float: the expected number of particles detected at the given
                location in one second
        """
        if point.amsl is not None:
            point_in_ecef = gps_to_ecef(point)
        else:
            point_in_ecef = None

        intensity = sum(source.intensity_at(point, point_in_ecef)
                        for source in self._sources)
        return (intensity + self.background_intensity)


construct = RadiationExtension
