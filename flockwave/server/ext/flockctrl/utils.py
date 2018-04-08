"""Utility functions."""

from struct import error as StructError

from .errors import ParseError

__all__ = ("unpack_struct", )


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
