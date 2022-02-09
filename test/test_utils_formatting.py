from flockwave.server.utils.formatting import format_list_nicely, format_uav_ids_nicely


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
