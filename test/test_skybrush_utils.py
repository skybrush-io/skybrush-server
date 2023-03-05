from pytest import raises

from flockwave.server.show.utils import (
    BoundingBoxCalculator,
    crc32_mavftp,
    encode_variable_length_integer,
)


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


def test_crc32_mavftp():
    data = (
        b"skyb\x02\x01\x00\x00\x00\x00\x01\x8d\x00\x01\x00\x00\x00\x00\x00\x00"
        b"\x00\x00 g\x04\x00\x00\xd5\x00\x80\x02\x10B\x1e\x90$ g\x04;&\x10'"
        b"\x10'\x02g\x04\x00\x00\xd5\x00\x80\x02\x01B\x1e\x90$\x02g\x04;&\x10'"
        b"\x10'\x08g\x04\x00\x00\xd5\x00\x80\x02\x04B\x1e\x90$\x08g\x04;&\x10'"
        b"\x10'\x00\xb8\x0b\n\xa9\x06\x10'\xb9%\n#\x10'\xb9%\n#\x05\xbe\x19\x06"
        b"\x04\x06\x04\n\xa9\x06W\x01\x00\x00\x00\x00W\x01\x00\x00\x00\x00 g"
        b"\x04\x10';&\x90$\x10B\x1e\x80\x02 g\x04\xd5\x00\x00\x00\x00\x00"
        b"\x02\x03\x00\x07\x00\x00"
    )

    assert crc32_mavftp(data, 0) == int.from_bytes(
        b"\xab\x5c\x53\x8a", "little", signed=False
    )
