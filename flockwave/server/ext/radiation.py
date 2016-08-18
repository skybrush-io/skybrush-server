"""Extension that allows one to place virtual "radiation sources" into
the world. Other extensions (such as ``fake_uav``) could then query the
locations of the radiation sources to provide UAVs with fake Geiger-Muller
counters.
"""

from __future__ import absolute_import

from flockwave.gps.vectors import Altitude, GPSCoordinate, \
    ECEFToGPSCoordinateTransformation

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
        self._location = None
        self._location_ecef = None

        self.location = GPSCoordinate(lat=lat, lon=lon, alt=Altitude.msl(0))
        self.intensity = max(float(intensity), 0.0)

    def intensity_at(self, ecef):
        """Returns the intensity of the radiation source at the given
        location, in ECEF coordinates.

        Parameters:
            ecef (ECEFCoordinate): the point of the query, in ECEF coordinates

        Returns:
            float: the intensity of the radiation source at the given point,
                i.e. the expected number of particles that would be detected
                in one second at the given point from the radiation source
        """
        dist = self._location_ecef.distance(ecef)
        return self.intensity / dist / dist

    @property
    def location(self):
        """The location of the radiation source as a GPSCoordinate_"""
        return self._location

    @location.setter
    def location(self, value):
        self._location = value
        self._location_ecef = gps_to_ecef(value)


class RadiationExtension(ExtensionBase):
    """Extension that allows one to place virtual "radiation sources" into
    the world.
    """

    def __init__(self):
        """Constructor."""
        super(RadiationExtension, self).__init__()
        self._background_intensity = 0.0
        self._sources = []
        self.exports = {
            "total_intensity_at": self._total_intensity_at
        }

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

    def _total_intensity_at(self, lat, lon, seconds=1):
        """Returns the total radiation intensity at the given latitude
        and longitude.

        The total radiation intensity is the number of particles that would
        be detected at the given latitude and longitude from the sources and
        the background radiation in the given time interval.

        Parameters:
            lat (float): the latitude of the point
            lon (float): the longitude of the point
            seconds (float): the total number of seconds

        Returns
            float: the expected number of particles detected at the given
                location in the given number of seconds
        """
        assert seconds >= 0
        if seconds == 0:
            return 0.0

        ecef = gps_to_ecef(GPSCoordinate(lat, lon))
        intensity = sum(source.intensity_at(ecef)
                        for source in self._sources)
        return (intensity + self.background_intensity) * seconds


construct = RadiationExtension
