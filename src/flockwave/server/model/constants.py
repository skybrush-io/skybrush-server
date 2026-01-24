"""Constants used in several places throughout the model."""

from __future__ import division

from math import pi

__all__ = (
    "WGS84",
    "GPS_PI",
    "PI_OVER_180",
    "SPEED_OF_LIGHT_M_S",
    "SPEED_OF_LIGHT_KM_S",
)


class WGS84(object):
    """WGS84 ellipsoid model parameters for Earth."""

    EQUATORIAL_RADIUS_IN_METERS = 6378137.0
    """Equatorial radius of Earth in the WGS ellipsoid model."""

    INVERSE_FLATTENING = 298.257223563
    """Inverse flattening of Earth in the WGS ellipsoid model."""

    FLATTENING = 1.0 / INVERSE_FLATTENING
    """Flattening of Earth in the WGS ellipsoid model."""

    ECCENTRICITY = (FLATTENING * (2 - FLATTENING)) ** 0.5
    """Eccentricity of Earth in the WGS ellipsoid model."""

    ECCENTRICITY_SQUARED = ECCENTRICITY**2
    """Square of the eccentricity of Earth in the WGS ellipsoid model."""

    POLAR_RADIUS_IN_METERS = EQUATORIAL_RADIUS_IN_METERS * (1 - FLATTENING)
    """Polar radius of Earth in the WGS ellipsoid model."""

    GRAVITATIONAL_CONSTANT_TIMES_MASS = 3.986005e14
    """Gravitational constant times Earth's mass."""

    ROTATION_RATE_IN_RADIANS_PER_SEC = 7.2921151467e-5
    """Earth's rotation rate [rad/sec]."""


PI_OVER_180 = pi / 180
"""Pi over 180; multiplicative constant to turn degrees into radians."""

GPS_PI = 3.1415926535898
"""Value of pi used in some GPS-specific calculations.."""

SPEED_OF_LIGHT_KM_S = 299792.458
"""Speed of light in km/s."""

SPEED_OF_LIGHT_M_S = 299792458.0
"""Speed of light in m/s."""
