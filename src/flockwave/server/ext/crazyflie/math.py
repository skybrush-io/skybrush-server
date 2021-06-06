"""Math related functions."""

from functools import lru_cache
from typing import List, Sequence, Tuple

__all__ = (
    "get_poly_degree",
    "to_bernstein_form",
)


@lru_cache(maxsize=64)
def pascal_triangle_row(index: int) -> Tuple[int, ...]:
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


def to_bernstein_form(poly: Sequence[float], eps: float = 0.0) -> List[float]:
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
