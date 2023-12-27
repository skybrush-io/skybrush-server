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


class TestTrajectorySpecification:
    spec_json = {
        "version": 1,
        "points": [
            [0, [10, 20, 0], []],
            [5, [10, 20, 20], [[10, 20, 0], [10, 20, 20]]],
            [15, [20, 20, 20], [[10, 20, 20], [20, 20, 20]]],
            [25, [20, 10, 20], [[20, 20, 20], [20, 10, 20]]],
            [30, [20, 10, 0], [[20, 10, 20], [20, 10, 0]]],
        ],
        "takeoffTime": 7,
    }
    spec = TrajectorySpecification(spec_json)
    empty_spec = TrajectorySpecification({"version": 1})

    def test_bounding_box(self):
        assert self.spec.bounding_box == ((10, 10, 0), (20, 20, 20))
        with raises(ValueError, match="empty"):
            _ = self.empty_spec.bounding_box

    def test_duration(self):
        assert self.empty_spec.duration == 0
        assert self.spec.duration == 30

    def test_get_padded_bounding_box(self):
        assert self.spec.get_padded_bounding_box(0) == self.spec.bounding_box
        assert self.spec.get_padded_bounding_box(5) == ((5, 5, -5), (25, 25, 25))
        with raises(ValueError, match="empty"):
            self.empty_spec.get_padded_bounding_box()

    def test_home_position(self):
        assert self.empty_spec.home_position == (0.0, 0.0, 0.0)
        assert self.spec.home_position == (10.0, 20.0, 0.0)

    def test_is_empty(self):
        assert self.empty_spec.is_empty
        assert not self.spec.is_empty

    def test_iter_segments(self):
        segments = list(self.spec.iter_segments())
        assert len(segments) == 4
        assert segments[0] == TrajectorySegment(
            t=0,
            duration=5,
            points=[[10, 20, 0], [10, 20, 0], [10, 20, 20], [10, 20, 20]],  # type: ignore
        )
        assert segments[1] == TrajectorySegment(
            t=5,
            duration=10,
            points=[[10, 20, 20], [10, 20, 20], [20, 20, 20], [20, 20, 20]],  # type: ignore
        )
        assert segments[2] == TrajectorySegment(
            t=15,
            duration=10,
            points=[[20, 20, 20], [20, 20, 20], [20, 10, 20], [20, 10, 20]],  # type: ignore
        )
        assert segments[3] == TrajectorySegment(
            t=25,
            duration=5,
            points=[[20, 10, 20], [20, 10, 20], [20, 10, 0], [20, 10, 0]],  # type: ignore
        )

    def test_iter_segments_absolute_time(self):
        segments = list(self.spec.iter_segments(absolute=True))
        assert len(segments) == 5
        assert segments[0] == TrajectorySegment(
            t=0,
            duration=7,
            points=[(10, 20, 0), [10, 20, 0]],  # type: ignore
        )
        assert segments[1] == TrajectorySegment(
            t=7,
            duration=5,
            points=[[10, 20, 0], [10, 20, 0], [10, 20, 20], [10, 20, 20]],  # type: ignore
        )
        assert segments[2] == TrajectorySegment(
            t=12,
            duration=10,
            points=[[10, 20, 20], [10, 20, 20], [20, 20, 20], [20, 20, 20]],  # type: ignore
        )
        assert segments[3] == TrajectorySegment(
            t=22,
            duration=10,
            points=[[20, 20, 20], [20, 20, 20], [20, 10, 20], [20, 10, 20]],  # type: ignore
        )
        assert segments[4] == TrajectorySegment(
            t=32,
            duration=5,
            points=[[20, 10, 20], [20, 10, 20], [20, 10, 0], [20, 10, 0]],  # type: ignore
        )

    def test_propose_scaling_factor(self):
        assert self.empty_spec.propose_scaling_factor() == 1
        assert self.spec.propose_scaling_factor() == 1

        from copy import deepcopy

        large_spec_json = deepcopy(self.spec_json)
        for record in large_spec_json["points"]:
            record[1] = [x * 10 for x in record[1]]

        # Largest coordinate will be 20 * 10 = 200m, which is 200000 mm.
        # We need a scaling factor of 7 because 200000 // 6 > 32767 but
        # 200000 // 7 < 32767
        assert TrajectorySpecification(large_spec_json).propose_scaling_factor() == 7

    def test_takeoff_time(self):
        assert self.empty_spec.takeoff_time == 0.0
        assert self.spec.takeoff_time == 7.0

    def test_errors(self):
        with raises(RuntimeError, match="must have a version number"):
            TrajectorySpecification({})
        with raises(RuntimeError, match="version 1"):
            TrajectorySpecification({"version": 66})
