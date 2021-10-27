from pytest import raises

from skybrush.trajectory import TrajectorySpecification


def test_trajectory_without_version_number():
    with raises(RuntimeError):
        TrajectorySpecification({})


def test_trajectory_with_invalid_version_number():
    with raises(RuntimeError):
        TrajectorySpecification({"version": -23})


def test_empty_trajectory():
    test_data = {"version": 1, "points": []}
    test_spec = TrajectorySpecification(test_data)

    assert test_spec.is_empty
    assert test_spec.home_position == (0, 0, 0)
    assert test_spec.landing_height == 0
    assert test_spec.propose_scaling_factor() == 1
    assert test_spec.takeoff_time == 0

    with raises(ValueError):
        test_spec.bounding_box
    with raises(ValueError):
        test_spec.get_padded_bounding_box()
    with raises(ValueError):
        test_spec.get_padded_bounding_box(margin=5)
