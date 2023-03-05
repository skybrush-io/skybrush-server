from pytest import approx

from flockwave.server.show.player import TrajectoryPlayer
from flockwave.server.show.trajectory import TrajectorySpecification


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

    player._select_segment(2)
    assert player.ended


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


def test_trajectory_player_bezier_curves():
    # Bezier curves obtained from:
    #
    # from skybrush.math.curves import create_cubic_spline
    # create_cubic_spline(
    #     points=[(0, 0, 0), (0, 3, 0), (3, 3, 0), (3, 0, 0)],
    #     knots=[0, 6, 12, 18],
    #     derivatives=(0, 0)
    # )
    test_data = {
        "version": 1,
        "points": [
            [0, [0, 0, 0], []],
            [6, [0, 3, 0], [[0, 0, 0], [-0.6, 2, 0]]],
            [12, [3, 3, 0], [[0.6, 4, 0], [2.4, 4, 0]]],
            [18, [3, 0, 0], [[3.6, 2, 0], [3, 0, 0]]],
        ],
        "takeoffTime": 3,
    }
    test_spec = TrajectorySpecification(test_data)
    player = TrajectoryPlayer(test_spec)

    assert not player.ended

    assert player.position_at(1) == (0, 0, 0)
    assert player.position_at(2.5) == (0, 0, 0)
    assert player.position_at(3) == (0, 0, 0)
    assert player.position_at(9) == (0, 3, 0)
    assert player.position_at(10.5) == (51 / 80, 3 + 9 / 16, 0)

    assert not player.ended

    assert player.position_at(12) == (1.5, 3.75, 0)
    assert player.position_at(13.5) == (189 / 80, 3 + 9 / 16, 0)
    assert player.position_at(15) == (3, 3, 0)
    assert player.position_at(21) == (3, 0, 0)
    assert player.position_at(24) == (3, 0, 0)

    assert player.ended

    assert player.is_before_takeoff(-2)
    assert player.is_before_takeoff(0)
    assert not player.is_before_takeoff(3)
    assert not player.is_before_takeoff(18)
    assert not player.is_before_takeoff(25)
