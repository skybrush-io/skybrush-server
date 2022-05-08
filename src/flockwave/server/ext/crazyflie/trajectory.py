"""Classes and functions for modeling trajectories in a format that is
understood by the Crazyflies.
"""

from dataclasses import dataclass
from enum import IntEnum
from struct import Struct
from typing import Sequence, Tuple

from skybrush.formats import SegmentEncoder
from skybrush.trajectory import TrajectorySpecification

from .math import get_poly_degree, to_bernstein_form


__all__ = ("encode_trajectory", "Poly4D", "to_poly4d_sequence")


class TrajectoryEncoding(IntEnum):
    """Enum representing the various trajectory encodings that the Crazyflie
    supports.
    """

    POLY4D = 0
    COMPRESSED = 1


@dataclass
class Poly4D:
    """Four-dimensional 7-th degree polynomial, used by the Crazyflie
    to represent trajectory segments. The four dimensions are: x, y, z and
    yaw.
    """

    duration: float
    xs: Tuple[float, ...] = (0.0,) * 8
    ys: Tuple[float, ...] = (0.0,) * 8
    zs: Tuple[float, ...] = (0.0,) * 8
    yaws: Tuple[float, ...] = (0.0,) * 8

    _float_coords_struct = Struct("<ffffffff")
    _duration_struct = Struct("<f")

    _compressed_header_struct = Struct("<BH")
    _short_coord_struct = Struct("<h")
    _short_coords_struct = Struct("<hhhh")

    def encode(self) -> bytes:
        """Encodes this Poly4D instance into a raw byte-level representation
        understood by the Crazyflie in its uncompressed trajectory format.

        Returns:
            the uncompressed representation of this trajectory segment
        """
        return b"".join(
            [
                self._float_coords_struct.pack(*self.xs),
                self._float_coords_struct.pack(*self.ys),
                self._float_coords_struct.pack(*self.zs),
                self._float_coords_struct.pack(*self.yaws),
                self._duration_struct.pack(self.duration),
            ]
        )

    def encode_compressed(self, with_start_point: bool = False) -> bytes:
        """Encodes this Poly4D instance into the compressed trajectory
        representation of the Crazyflie.

        Parameters:
            with_start_point: whether th prepend the Poly4D data with the
                start point; this must be used for the first Poly4D segment
                in the encoding

        Returns:
            the compressed representation of this trajectory segment
        """
        formats = []
        parts = []

        all_polys_and_scales = (
            (self.xs, 1000),  # scaling factor: 1m = 1000 units
            (self.ys, 1000),
            (self.zs, 1000),
            (self.yaws, 10),  # scaling factor: 1 degree = 10 units
        )

        for poly, scale in all_polys_and_scales:
            # Rescale the argument of the parametric curve to the [0; 1] range
            if self.duration != 1:
                poly = [x * (self.duration**i) for i, x in enumerate(poly)]

            # Encode polynomial coefficients in Bernstein form
            format, data = self._encode_polynomial_compressed(poly, scale)

            # Store the data
            formats.append(format)
            parts.append(data)

        header = 0
        header |= formats[0]
        header |= formats[1] << 2
        header |= formats[2] << 4
        header |= formats[3] << 6

        duration = int(round(self.duration * 1000))
        parts.insert(0, self._compressed_header_struct.pack(header, duration))

        if with_start_point:
            parts.insert(
                0,
                self._short_coords_struct.pack(
                    *[round(poly[0] * scale) for poly, scale in all_polys_and_scales]
                ),
            )

        return b"".join(parts)

    @classmethod
    def _encode_polynomial_compressed(
        cls, coeffs: Sequence[float], scale: int = 1000, *, eps: float = 1e-7
    ) -> Tuple[int, bytes]:
        """Encodes the coefficients of the given polynomial into the compressed
        byte-level representation of the Crazyflie, retuning the chosen
        compression scheme and the raw bytes.

        The 0-degree coefficient will _not_ be encoded; we don't need it when
        we encode continuous curves as the start of a segment is the same as
        the end of the previous segment, which we know. The remaining
        nonzero coefficients will be encoded as unsigned short integers such
        that the raw value of the coefficient is multiplied by 1000 and then
        rounded to the nearest integer.

        Parameters:
            coeffs: raw coefficients of the polynomial to encode
            eps: threshold below which a coefficient is treated as zero
            scale: scaling factor to use when encoding the coordinates of
                the control points in Bernstein form as integers

        Returns:
            a tuple consisting of the chosen compression scheme (0 = constant,
            1 = linear, 2 = cubic, 3 = 7-th degree polynomial), and the raw
            bytes that encode the coefficients.
        """
        degree = get_poly_degree(coeffs, eps=eps)
        coeffs = to_bernstein_form(coeffs, eps=eps)
        if len(coeffs) < degree + 1:
            coeffs += [0] * (degree + 1 - len(coeffs))

        if degree <= 0:
            format = 0
        elif degree <= 1:
            format = 1
        elif degree <= 3:
            format = 2
        elif degree <= 7:
            format = 3
        else:
            raise ValueError(
                "polynomials with nonzero coefficients above the 7th degree "
                "are not supported"
            )

        data = b"".join(
            cls._short_coord_struct.pack(int(round(coeff * scale)))
            for coeff in coeffs[1:]
        )
        return format, data


def encode_trajectory(
    trajectory: TrajectorySpecification,
    *,
    encoding: TrajectoryEncoding = TrajectoryEncoding.POLY4D,
) -> bytes:
    """Returns a byte-level representation of the given sequence of Poly4D
    segments.

    Parameters:
        segments: the segments to encode
        encoding: the encoding format to use

    Returns:
        the encoded trajectory
    """
    if encoding is TrajectoryEncoding.POLY4D:
        polynomials = to_poly4d_sequence(trajectory)
        result = b"".join(polynomial.encode() for polynomial in polynomials)
    else:
        encoder = SegmentEncoder(scale=1)
        encoded = encoder.iter_encode_multiple_segments(
            trajectory.iter_segments(max_length=65)
        )
        result = b"".join(encoded) + b"\x00\x00\x00"

    return result


def to_poly4d_sequence(trajectory: TrajectorySpecification) -> Sequence[Poly4D]:
    result = []

    for segment in trajectory.iter_segments(max_length=65):
        if segment.has_control_points:
            raise ValueError("control points are not implemented yet")

        start, end = segment.start, segment.end
        dx, dy, dz = end[0] - start[0], end[1] - start[1], end[2] - start[2]
        dt = segment.duration

        xs = (start[0], dx / dt, 0, 0, 0, 0, 0, 0)
        ys = (start[1], dy / dt, 0, 0, 0, 0, 0, 0)
        zs = (start[2], dz / dt, 0, 0, 0, 0, 0, 0)

        result.append(Poly4D(duration=dt, xs=xs, ys=ys, zs=zs))

    return result
