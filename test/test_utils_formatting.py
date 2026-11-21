from datetime import timedelta

from flockwave.server.utils.formatting import (
    format_list_nicely,
    format_timedelta_nicely,
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


def test_format_timedelta_nicely():
    fmt = format_timedelta_nicely

    assert fmt(0) == "00:00:00"
    assert fmt(5.5) == "00:00:05.5"
    assert fmt(3723) == "01:02:03"
    assert fmt(-90) == "-00:01:30"
    assert fmt(timedelta(minutes=2, seconds=7.25)) == "00:02:07.25"

    # Milliseconds are kept, not cut to three significant digits
    assert fmt(12.345) == "00:00:12.345"
    assert fmt(754.125) == "00:12:34.125"

    # Seconds that round up to a full minute carry over to the minutes
    assert fmt(59.9996) == "00:01:00"
    assert fmt(3599.9999) == "01:00:00"
    assert fmt(59.95) == "00:00:59.95"
