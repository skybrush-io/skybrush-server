"""Tests for the `lps_dummy` extension."""

from collections.abc import Mapping
from contextlib import aclosing
from math import isclose, sqrt
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pytest import mark, raises
from trio import current_time, open_nursery, sleep
from trio.testing import wait_all_tasks_blocked

from flockwave.server.ext import lps_dummy
from flockwave.server.ext.lps.extension import LocalPositioningSystemsExtension
from flockwave.server.ext.lps_dummy import (
    CALIBRATION_STEPS,
    DummyLocalPositioningSystem,
    DummyLocalPositioningSystemType,
)
from flockwave.server.model import Quaternion
from flockwave.server.model.commands import Progress, Suspend
from flockwave.server.registries import ObjectRegistry

from .recording_message_hub import RecordingMessageHub

Vector = tuple[float, float, float]


def rotate(q: Quaternion, v: Vector) -> Vector:
    """Rotates the given vector with the given quaternion."""
    w, qx, qy, qz = q
    x, y, z = v
    # t = 2 * cross(q.xyz, v); v' = v + w * t + cross(q.xyz, t)
    tx, ty, tz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
    return (
        x + w * tx + (qy * tz - qz * ty),
        y + w * ty + (qz * tx - qx * tz),
        z + w * tz + (qx * ty - qy * tx),
    )


def assert_vectors_close(actual: Vector, expected: Vector) -> None:
    assert all(isclose(a, e, abs_tol=1e-9) for a, e in zip(actual, expected)), (
        actual,
        expected,
    )


def create_dummy_lps() -> DummyLocalPositioningSystem:
    lps = DummyLocalPositioningSystem()
    lps._id = "dummy"
    lps._schema_validator_getter = (
        DummyLocalPositioningSystemType().get_configuration_schema_validator
    )
    return lps


def test_dummy_lps_anchors_face_the_origin():
    lps = create_dummy_lps()
    assert [anchor.id for anchor in lps.anchors] == ["1", "2", "3", "4"]

    for anchor in lps.anchors:
        assert anchor.position is not None and anchor.attitude is not None
        assert isclose(sum(c * c for c in anchor.attitude), 1)

        x, y, z = anchor.position.x, anchor.position.y, anchor.position.z
        assert (abs(x), abs(y), z) == (2, 2, 2.5)

        # The X axis of the anchor points towards the origin
        length = sqrt(x * x + y * y + z * z)
        assert_vectors_close(
            rotate(anchor.attitude, (1, 0, 0)), (-x / length, -y / length, -z / length)
        )

        # The anchor is not rolled; its Y axis stays horizontal
        assert isclose(rotate(anchor.attitude, (0, 1, 0))[2], 0, abs_tol=1e-9)


async def test_dummy_lps_configuration_updates_anchor_geometry():
    lps = create_dummy_lps()
    updates = []
    cfg = {"width": 6.0, "depth": 3.0, "height": 2.0, "simulate_faults": False}

    with lps.on_updated.connected_to(updates.append, sender=lps):
        await lps.configure(cfg)

    assert updates == [lps]
    assert await lps.get_configuration() == cfg
    position = lps.anchors[0].position
    assert position is not None
    assert (position.x, position.y, position.z) == (3, 1.5, 2)


async def test_dummy_lps_rejects_invalid_configuration():
    lps = create_dummy_lps()
    with raises(RuntimeError, match="Invalid configuration"):
        await lps.configure(
            {"width": -1.0, "depth": 3.0, "height": 2.0, "simulate_faults": False}
        )


async def test_dummy_lps_simulates_faults(autojump_clock):
    lps = create_dummy_lps()
    anchor = lps.anchors[-1]

    async with open_nursery() as nursery:
        nursery.start_soon(lps.run)
        states = []
        for _ in range(4):
            await sleep(lps.fault_cycle_period)
            await wait_all_tasks_blocked()
            states.append((anchor.active, list(anchor.errors)))
        nursery.cancel_scope.cancel()

    assert states == [
        (True, [127]),
        (True, [137]),
        (False, []),
        (True, []),
    ]
    assert all(a.active and not a.errors for a in lps.anchors[:-1])


async def test_dummy_lps_does_not_simulate_faults_when_disabled(autojump_clock):
    lps = create_dummy_lps()
    await lps.configure(
        {"width": 4.0, "depth": 4.0, "height": 2.5, "simulate_faults": False}
    )

    async with open_nursery() as nursery:
        nursery.start_soon(lps.run)
        await sleep(lps.fault_cycle_period * 5)
        nursery.cancel_scope.cancel()

    assert all(a.active and not a.errors for a in lps.anchors)


async def test_extension_registers_dummy_lps_while_loaded(hub: RecordingMessageHub):
    lps_ext = LocalPositioningSystemsExtension()
    lps_ext.app = SimpleNamespace(  # type: ignore
        command_execution_manager=MagicMock(),
        message_hub=hub,
        object_registry=ObjectRegistry(),
    )
    app = SimpleNamespace(
        import_api=lambda name, *args: SimpleNamespace(**lps_ext.exports())
    )

    async with open_nursery() as nursery:
        nursery.start_soon(lps_ext.run)
        await wait_all_tasks_blocked()

        async with open_nursery() as dummy_nursery:
            dummy_nursery.start_soon(lps_dummy.run, app)
            await wait_all_tasks_blocked()

            assert list(lps_ext._lps_type_registry.ids) == ["dummy"]
            assert list(lps_ext._lps_registry.ids) == ["dummy"]
            assert isinstance(
                lps_ext._lps_registry.find_by_id("dummy"), DummyLocalPositioningSystem
            )
            assert [list(body["status"]) for body in hub.broadcasts] == [["dummy"]]

            # Unload the extension
            dummy_nursery.cancel_scope.cancel()

        # The dummy LPS and its type are removed while the lps extension
        # keeps running
        assert list(lps_ext._lps_registry.ids) == []
        assert list(lps_ext._lps_type_registry.ids) == []

        nursery.cancel_scope.cancel()


async def run_calibration(
    lps: DummyLocalPositioningSystem, params: Mapping[str, Any], answers: list[Any]
) -> list[tuple[float, Any]]:
    """Runs the calibration of the given LPS in the same way as the command
    execution manager does, answering each suspension with the next item
    from the given list.

    Returns:
        the items yielded by the calibration and the times they were yielded at
    """
    events: list[tuple[float, Any]] = []
    remaining_answers = iter(answers)
    value = None
    async with aclosing(lps.calibrate(params)) as gen:
        while True:
            try:
                event = await gen.asend(value)
            except StopAsyncIteration:
                break
            events.append((current_time(), event))
            value = next(remaining_answers) if isinstance(event, Suspend) else None
    return events


async def test_dummy_lps_calibration_walks_through_all_steps(autojump_clock):
    lps = create_dummy_lps()
    start = current_time()
    events = await run_calibration(
        lps, {"sample_duration": 2.5}, [True] * len(CALIBRATION_STEPS)
    )

    suspensions = [event for _, event in events if isinstance(event, Suspend)]
    assert [s.message for s in suspensions] == list(CALIBRATION_STEPS)
    assert [s.object for s in suspensions] == [
        {"step": 1, "steps": 3},
        {"step": 2, "steps": 3},
        {"step": 3, "steps": 3},
    ]

    progress = [event for _, event in events if isinstance(event, Progress)]
    percentages = [p.percentage for p in progress]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100
    assert progress[-1].message == "Calibration finished"

    assert isclose(current_time() - start, 2.5 * len(CALIBRATION_STEPS))


async def test_dummy_lps_calibration_can_be_aborted(autojump_clock):
    lps = create_dummy_lps()
    with raises(RuntimeError, match="aborted"):
        await run_calibration(lps, {"sample_duration": 1}, [True, False])


async def test_dummy_lps_calibration_reports_progress_regularly(autojump_clock):
    lps = create_dummy_lps()
    events = await run_calibration(
        lps, {"sample_duration": 100}, [True] * len(CALIBRATION_STEPS)
    )
    times = [time for time, _ in events]
    assert max(b - a for a, b in zip(times, times[1:])) <= 1


@mark.parametrize(
    "params",
    [{"sample_duration": 0}, {"sample_duration": True}, {"foo": 1}],
)
async def test_dummy_lps_calibration_rejects_invalid_parameters(
    params: dict[str, Any],
):
    lps = create_dummy_lps()
    with raises(RuntimeError):
        await run_calibration(lps, params, [])
