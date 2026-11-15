"""Math related functions."""

from collections.abc import Sequence
from functools import lru_cache
from math import sqrt

from flockwave.server.model import Quaternion

__all__ = (
    "Matrix3x3",
    "get_poly_degree",
    "quaternion_from_rotation_matrix",
    "rotation_matrix_from_quaternion",
    "to_bernstein_form",
)


Matrix3x3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
"""Type alias for a 3x3 matrix, given as a tuple of rows."""


@lru_cache(maxsize=64)
def pascal_triangle_row(index: int) -> tuple[int, ...]:
    """Returns the given row of the Pascal triangle.

    This function is memoized.

    Parameters:
        index: the row index; the triangle starts from row 0.

    Returns:
        the given row of the Pascal triangle
    """
    assert index >= 0 and index < 64
    if index == 0:
        return (1,)
    else:
        previous_row = pascal_triangle_row(index - 1)
        prev = 0
        current_row = []
        for curr in previous_row:
            current_row.append(prev + curr)
            prev = curr
        current_row.append(prev)
        return tuple(current_row)


def get_poly_degree(poly: Sequence[float], eps: float = 0.0) -> int:
    """Returns the degree of the given polynomial.

    Parameters:
        poly: the coefficients of the polynomial terms, starting from the
            zero-degree term
        eps: tolerance threshold to determine whether a coefficient is zero

    Returns
        the degree of the polynomial
    """
    degree = len(poly) - 1
    while degree > 0:
        if abs(poly[degree]) > eps:
            return degree
        degree -= 1
    return 0


def to_bernstein_form(poly: Sequence[float], eps: float = 0.0) -> list[float]:
    """Converts a polynomial given with its coefficients to the corresponding
    coefficients in its Bernstein form.

    Zero coefficients will be eliminated from the end of the input. The given
    epsilon parameter determines what constitutes as a zero coefficient.

    Parameters:
        poly: the coefficients of the polynomial terms, starting from the
            zero-degree term
        eps: tolerance threshold to determine whether a coefficient is zero

    Returns:
        the Bernstein coefficients of the polynomial
    """
    degree = get_poly_degree(poly, eps)
    divisors = pascal_triangle_row(degree)
    coeffs = [coeff / divisor for coeff, divisor in zip(poly, divisors)]

    result = []
    while coeffs:
        result.append(coeffs[0])
        for index in range(len(coeffs) - 1):
            coeffs[index] += coeffs[index + 1]
        coeffs.pop()

    return result[: (get_poly_degree(result, eps) + 1)]


def quaternion_from_rotation_matrix(
    matrix: Sequence[Sequence[float]],
) -> Quaternion:
    """Converts a 3x3 rotation matrix into a unit quaternion.

    The matrix is given as a sequence of rows. It is assumed to rotate
    vectors when multiplied from the left, i.e. ``v' = M v``. Matrices that
    are only approximately orthonormal (e.g., due to the limited precision of
    single-precision floats) are accepted; the result is normalized.

    Parameters:
        matrix: the rotation matrix, row by row

    Returns:
        the corresponding unit quaternion in Hamilton conventions, i.e. in
        ``(w, x, y, z)`` order, with a non-negative ``w`` component

    Raises:
        ValueError: if the matrix is degenerate or is not a proper rotation
    """
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = matrix

    # The determinant of a rotation matrix is 1. Anything far from that is
    # either degenerate or a reflection
    det = (
        m00 * (m11 * m22 - m12 * m21)
        - m01 * (m10 * m22 - m12 * m20)
        + m02 * (m10 * m21 - m11 * m20)
    )
    if det < 0.5:
        raise ValueError("matrix is not a rotation matrix")

    # Shepperd's method: pick the largest of the four possible pivots to
    # avoid dividing by a number close to zero
    trace = m00 + m11 + m22
    if trace > 0:
        s = 2 * sqrt(trace + 1)
        w, x, y, z = s / 4, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = 2 * sqrt(1 + m00 - m11 - m22)
        w, x, y, z = (m21 - m12) / s, s / 4, (m01 + m10) / s, (m02 + m20) / s
    elif m11 > m22:
        s = 2 * sqrt(1 + m11 - m00 - m22)
        w, x, y, z = (m02 - m20) / s, (m01 + m10) / s, s / 4, (m12 + m21) / s
    else:
        s = 2 * sqrt(1 + m22 - m00 - m11)
        w, x, y, z = (m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, s / 4

    norm = sqrt(w * w + x * x + y * y + z * z)
    if w < 0:
        norm = -norm

    return (w / norm, x / norm, y / norm, z / norm)


def rotation_matrix_from_quaternion(q: Quaternion) -> Matrix3x3:
    """Converts a unit quaternion into a 3x3 rotation matrix.

    This is the inverse of `quaternion_from_rotation_matrix()`.

    Parameters:
        q: the unit quaternion in Hamilton conventions, i.e. in
            ``(w, x, y, z)`` order

    Returns:
        the rotation matrix, row by row. The matrix rotates vectors when
        multiplied from the left, i.e. ``v' = M v``.
    """
    w, x, y, z = q
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )
