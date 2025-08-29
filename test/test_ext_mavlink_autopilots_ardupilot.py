from pathlib import Path
from flockwave.server.ext.mavlink.autopilots.ardupilot import (
    decode_parameters_from_packed_format,
    encode_parameters_to_packed_format,
)
from flockwave.server.ext.mavlink.enums import MAVParamType


def test_decode_parameters_from_packed_format(datadir: Path) -> None:
    with (datadir / "param.pck").open("rb") as fp:
        params = list(decode_parameters_from_packed_format(fp))

    assert len(params) == 1431

    # Smoke test on a few parameters
    param_map = {param.name.decode("ascii"): param for param in params}
    assert param_map["SYSID_THISMAV"].value == 3
    assert param_map["PILOT_THR_BHV"].value == 0
    assert param_map["PILOT_THR_BHV"].type == MAVParamType.INT16
    assert param_map["SHOW_LED0_TYPE"].value == 6
    assert param_map["SHOW_LED0_TYPE"].type == MAVParamType.INT8


def test_encode_parameters_to_packed_format() -> None:
    params = {
        "SHOW_START_TIME": 7654321,
        "SHOW_START_AUTH": 1,
        "show_origin_lat": 47.12345,  # intentionally lowercase
        "SHOW_ORIGIN_LON": 8.12345,
        "FENCE_ENABLE": 1,
        "FENCE_ACTION": 2,
        # Add two parameters where one is a prefix of the other
        "FOO": 3,
        "FOOBAR": 4,
        # Add two parameters that differ only in their last character
        "FROB1": 5,
        "FROB2": 6,
    }
    packed = encode_parameters_to_packed_format(params)
    assert packed == (
        # fmt: off
        b"\x1b\x67\x0a\x00h\x00"
        b"\x01\xb0FENCE_ACTION"
        b"\x02\x01\x56ENABLE\x01"
        b"\x01\x11OO\x03"
        b"\x01\x23BAR\x04"
        b"\x01\x31ROB1\x05"
        b"\x01\x042\x06"
        b"\x04\xe0SHOW_ORIGIN_LATj~<B"
        b"\x04\x1dON\xa7\xf9\x01A"
        b"\x01\x95START_AUTH\x01"
        b"\x03\x3bTIME\xb1\xcbt\x00"
        # fmt: on
    )
