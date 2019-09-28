"""Utility functions."""

from struct import error as StructError

from flockwave.gps.vectors import GPSCoordinate

from .errors import ParseError

__all__ = ("unpack_struct",)


def unpack_struct(spec, data):
    """Unpacks some data from the given raw bytes object according to
    the format specification (given as a Python struct).

    This method is a thin wrapper around ``struct.Struct.unpack()`` that
    turns ``struct.error`` exceptions into ParseError_.

    Parameters:
        spec (Optional[Struct]): the specification of the format of the
            byte array to unpack.
        data (bytes): the bytes to unpack

    Returns:
        tuple: the unpacked values as a tuple and the remainder of the
            data that was not parsed using the specification

    Raises:
        ParseError: if the given byte array cannot be unpacked
    """
    size = spec.size
    try:
        return spec.unpack(data[:size]), data[size:]
    except StructError as ex:
        raise ParseError(str(ex))


def convert_mkgps_position_to_gps_coordinate(lat, lon, amsl, agl):
    """Standardize units coming from the mkgps_position_t packet and store
    results as a GPSCoordinate object.

    Parameters:
        lat: latitude in [1e-7 deg]
        lon: longitude in [1e-7 deg]
        amsl: above mean sea level in [dm]
        agl: above ground level in [dm]

    Returns:
        GPSCoordinate object with member values in SI units

    """

    return GPSCoordinate(
        lat=lat / 1e7,  # [1e-7 deg] --> [deg]
        lon=lon / 1e7,  # [1e-7 deg] --> [deg]
        amsl=amsl / 1e1,  # [dm]       --> [m]
        agl=agl / 1e1,  # [dm]       --> [m]
    )
