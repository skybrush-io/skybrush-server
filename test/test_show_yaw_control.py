from pytest import raises

from flockwave.server.show.yaw import RelativeYawSetpoint, YawSetpoint, YawSetpointList


def test_yaw_setpoints_without_version_number():
    with raises(ValueError):
        YawSetpointList.from_json({})


def test_yaw_setpoints_with_invalid_version_number():
    with raises(ValueError):
        YawSetpointList.from_json({"version": -23})


def test_yaw_setpoints_and_auto_yaw():
    with raises(ValueError):
        YawSetpointList.from_json(
            {"version": 1, "setpoints": [(0, 0)], "autoYaw": True}
        )


def test_empty_yaw_setpoints():
    test_data = {"version": 1, "setpoints": []}
    test_spec = YawSetpointList.from_json(test_data)

    assert test_spec.auto_yaw is False
    assert test_spec.auto_yaw_offset == 0
    assert test_spec.setpoints == []
    assert test_spec.yaw_offset == 0


def test_iter_yaw_setpoints():
    test_data = {
        "version": 1,
        "setpoints": [
            YawSetpoint(0, 0),
            YawSetpoint(1, 1),
            YawSetpoint(6, 31),
            YawSetpoint(11, 91),
            YawSetpoint(23, 94),
        ],
    }
    test_spec = YawSetpointList.from_json(test_data)
    expected_result = [
        RelativeYawSetpoint(0, 0),
        RelativeYawSetpoint(1, 1),
        RelativeYawSetpoint(5, 30),
        RelativeYawSetpoint(2.5, 30),
        RelativeYawSetpoint(2.5, 30),
        RelativeYawSetpoint(4, 1),
        RelativeYawSetpoint(4, 1),
        RelativeYawSetpoint(4, 1),
    ]

    assert test_spec.auto_yaw is False
    assert test_spec.auto_yaw_offset == 0
    for delta, expected in zip(
        test_spec.iter_setpoints_as_relative(max_duration=5, max_yaw_change=30),
        expected_result,
        strict=True,
    ):
        assert delta == expected
