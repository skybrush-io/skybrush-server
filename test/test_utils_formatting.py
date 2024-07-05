from pytest import mark

from flockwave.server.utils.formatting import (
    format_latitude_for_nmea_gga_message,
    format_list_nicely,
    format_longitude_for_nmea_gga_message,
    format_uav_ids_nicely,
)


def spamify(x: str) -> str:
    return f"{x} spam"


def test_format_list_nicely():
    fmt = format_list_nicely

    assert fmt([]) == ""
    assert fmt(["foo"]) == "foo"
    assert fmt(["foo", "bar"]) == "foo and bar"
    assert fmt(["spam", "ham", "eggs"]) == "spam, ham and eggs"
    assert fmt(["spam"] * 8) == "spam, spam, spam, spam, spam and 3 more"
    assert fmt(["spam"] * 4, max_items=4) == "spam, spam, spam and spam"
    assert (
        fmt(["lovely", "wonderful"], item_formatter=spamify)
        == "lovely spam and wonderful spam"
    )


def test_format_uav_ids_nicely():
    fmt = format_uav_ids_nicely

    assert fmt(("17", "34", "81")) == "UAVs 17, 34 and 81"
    assert fmt(()) == "no UAVs"
    assert fmt(("42",)) == "UAV 42"


@mark.parametrize(
    ("input", "output"),
    [
        (-1.8, ("0148.0000", "S")),
        (-1.75, ("0145.0000", "S")),
        (-1.9, ("0154.0000", "S")),
        (-2, ("0200.0000", "S")),
        (-2.025, ("0201.5000", "S")),
        (1.8, ("0148.0000", "N")),
        (1.75, ("0145.0000", "N")),
        (1.9, ("0154.0000", "N")),
        (2, ("0200.0000", "N")),
        (2.025, ("0201.5000", "N")),
        (39 + 7.356 / 60, ("3907.3560", "N")),
    ],
)
def test_format_latitude_for_nmea_gga_message(input: float, output: tuple[str, str]):
    assert format_latitude_for_nmea_gga_message(input) == output


@mark.parametrize(
    ("input", "output"),
    [
        (-1.8, ("00148.0000", "W")),
        (-1.75, ("00145.0000", "W")),
        (-1.9, ("00154.0000", "W")),
        (-2, ("00200.0000", "W")),
        (-2.025, ("00201.5000", "W")),
        (1.8, ("00148.0000", "E")),
        (1.75, ("00145.0000", "E")),
        (1.9, ("00154.0000", "E")),
        (2, ("00200.0000", "E")),
        (123.025, ("12301.5000", "E")),
        (-(121 + 2.482 / 60), ("12102.4820", "W")),
    ],
)
def test_format_longitude_for_nmea_gga_message(input: float, output: tuple[str, str]):
    assert format_longitude_for_nmea_gga_message(input) == output
