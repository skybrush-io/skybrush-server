from pytest import raises

from skybrush.utils import BoundingBoxCalculator, encode_variable_length_integer


def test_encode_variable_length_integer():
    encode = encode_variable_length_integer

    assert encode(0) == b"\x00"
    assert encode(17) == b"\x11"
    assert encode(127) == b"\x7f"
    assert encode(128) == b"\x80\x01"
    assert encode(255) == b"\xff\x01"
    assert encode(42315) == b"\xcb\xca\x02"

    with raises(ValueError):
        encode(-4)

    with raises(TypeError):
        encode(1.5)  # type: ignore

    with raises(ValueError):
        encode(-4.5)  # type: ignore


def test_bounding_box():
    calc = BoundingBoxCalculator(dim=2)

    assert calc.is_empty
    with raises(ValueError, match="empty"):
        calc.get_corners()

    calc.add((5, 2))
    calc.add((3, 7))
    calc.add((-2, 3))
    calc.add((2, 21))

    assert calc.get_corners() == ((-2, 2), (5, 21))

    calc.pad(3)
    assert calc.get_corners() == ((-5, -1), (8, 24))

    with raises(ValueError, match="negative"):
        calc.pad(-2)
