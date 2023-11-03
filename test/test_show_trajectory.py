from pytest import approx, raises

from flockwave.server.show.player import create_function_for_segment
from flockwave.server.show.trajectory import TrajectorySegment, TrajectorySpecification
from flockwave.server.show.utils import Point


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
        _ = test_spec.bounding_box
    with raises(ValueError):
        test_spec.get_padded_bounding_box()
    with raises(ValueError):
        test_spec.get_padded_bounding_box(margin=5)


def test_trajectory_segment_splitting():
    points: list[Point] = [(6, 6, 1), (12, 6, 1), (12, 12, 1), (6, 12, 1)]
    segment = TrajectorySegment(t=5, duration=6, points=points)

    # Splitting at the start
    head, tail = segment.split_at(0)
    assert head == TrajectorySegment(t=5, duration=0, points=[points[0]])
    assert tail is segment

    # Splitting at the end
    head, tail = segment.split_at(1)
    assert head is segment
    assert tail == TrajectorySegment(t=11, duration=0, points=[points[-1]])

    # Splitting at one third
    head, tail = segment.split_at(1 / 3)
    assert head.t == 5
    assert head.duration == 2
    assert head.points == [
        (6, 6, 1),
        (8, 6, 1),
        approx((9 + 1 / 3, 6 + 2 / 3, 1)),
        approx((10, 7 + 5 / 9, 1)),
    ]
    assert tail.t == 7
    assert tail.duration == 4
    assert tail.points == [
        approx((10, 7 + 5 / 9, 1)),
        approx((11 + 1 / 3, 9 + 1 / 3, 1)),
        approx((10, 12, 1)),
        approx((6, 12, 1)),
    ]

    # Invalid fraction
    with raises(ValueError, match="fraction must be between 0 and 1"):
        segment.split_at(-2)


def test_trajectory_segment_splitting_to_max_duration():
    points: list[Point] = [(6, 6, 1), (12, 6, 1), (12, 12, 1), (6, 12, 1)]
    segment = TrajectorySegment(t=5, duration=6, points=points)

    # Split into two pieces
    split_segments = list(segment.split_to_max_duration(4))

    assert len(split_segments) == 2
    head, tail = split_segments
    assert head.t == 5
    assert head.duration == 3
    assert head.points == [(6, 6, 1), (9, 6, 1), (10.5, 7.5, 1), (10.5, 9, 1)]
    assert tail.t == 8
    assert tail.duration == 3
    assert tail.points == [(10.5, 9, 1), (10.5, 10.5, 1), (9, 12, 1), (6, 12, 1)]

    # Split into six pieces
    split_segments = list(segment.split_to_max_duration(1.2))

    assert len(split_segments) == 6
    for index, fragment in enumerate(split_segments):
        assert fragment.t == 5 + index
        assert fragment.duration == 1
        assert len(fragment.points) == 4

        bezier = create_function_for_segment(segment)
        assert fragment.points[0] == approx(bezier(index / 6))
        assert fragment.points[-1] == approx(bezier((index + 1) / 6))

    # Split with invalid duration
    with raises(ValueError, match="must be positive"):
        list(segment.split_to_max_duration(-2))
