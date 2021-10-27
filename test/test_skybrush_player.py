from pytest import approx

from skybrush.player import TrajectoryPlayer
from skybrush.trajectory import TrajectorySpecification


def test_trajectory_player_empty_trajectory():
    test_data = {"version": 1, "points": []}
    test_spec = TrajectorySpecification(test_data)
    player = TrajectoryPlayer(test_spec)

    assert player.position_at(1) == (0, 0, 0)
    assert player.position_at(4) == (0, 0, 0)
    assert player.position_at(8) == (0, 0, 0)
    assert player.position_at(-2) == (0, 0, 0)

    assert player.is_before_takeoff(-2)
    assert not player.is_before_takeoff(0)
    assert not player.is_before_takeoff(6)


def test_trajectory_player_linear_segments_only():
    test_data = {
        "version": 1,
        "points": [
            [19, [-2.5, 10, 15], []],
            [19.5, [-2.5, 10, 15], []],
            [20, [-2.27, 10.17, 15.17], []],
            [20.5, [-1.34, 10.85, 15.85], []],
            [21, [0.02, 11.86, 16.86], []],
            [21.5, [1.41, 12.88, 17.88], []],
            [22, [2.79, 13.9, 18.9], []],
        ],
        "takeoffTime": 3,
    }
    test_spec = TrajectorySpecification(test_data)
    player = TrajectoryPlayer(test_spec)

    assert player.position_at(18) == (-2.5, 10, 15)
    assert player.position_at(21) == (-2.5, 10, 15)
    assert player.position_at(21.5) == (-2.5, 10, 15)
    assert player.position_at(22) == (-2.5, 10, 15)
    assert player.position_at(21.5) == (-2.5, 10, 15)
    assert player.position_at(22) == (-2.5, 10, 15)
    assert player.position_at(22.2) == (-2.5, 10, 15)
    assert player.position_at(22.4) == (-2.5, 10, 15)
    assert player.position_at(22.6) == approx((-2.454, 10.034, 15.034))
    assert player.position_at(22.8) == approx((-2.362, 10.102, 15.102))
    assert player.position_at(22.999) == approx((-2.27046, 10.16966, 15.16966))
    assert player.position_at(23) == approx((-2.27, 10.17, 15.17))
    assert player.position_at(24.2) == approx((0.576, 12.268, 17.268))
    assert player.position_at(24.7) == approx((1.962, 13.288, 18.288))
    assert player.position_at(23) == approx((-2.27, 10.17, 15.17))
    assert player.position_at(18) == (-2.5, 10, 15)
    assert player.position_at(25) == (2.79, 13.9, 18.9)
    assert player.position_at(28) == (2.79, 13.9, 18.9)

    assert player.is_before_takeoff(-2)
    assert player.is_before_takeoff(0)
    assert not player.is_before_takeoff(3)
    assert not player.is_before_takeoff(18)
    assert not player.is_before_takeoff(25)
    assert not player.is_before_takeoff(30)
