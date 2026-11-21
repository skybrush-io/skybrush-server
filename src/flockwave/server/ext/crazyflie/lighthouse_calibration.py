"""Estimation of the geometry of Lighthouse base stations with the help of a
single Crazyflie drone.

The user places the drone at the origin of the coordinate system, at a few
points on the positive X axis and at a few other points on the floor, then
carries it around the area covered by the base stations. The geometry of the
base stations is estimated from the sweep angles that the drone measures, and
the user reviews it before it is applied.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import acos, degrees, dist
from typing import TYPE_CHECKING, Any

from aiocflib.crazyflie.lighthouse import LighthouseBsGeometry, LighthouseConfiguration
from aiocflib.lighthouse import (
    BaseStationPlacementError,
    CalibrationSamples,
    GeometryEstimate,
    RoomAlignmentError,
    Sample,
    estimate_geometry,
)
from trio import to_thread

from flockwave.server.model.commands import (
    Progress,
    ProgressEventsWithSuspension,
    Suspend,
)

if TYPE_CHECKING:
    from .lighthouse import LighthouseUAV

__all__ = (
    "LighthouseGeometryEstimation",
    "LighthouseGeometryEstimationOptions",
)


STATIC_SAMPLE_DURATION = 5.0
"""Number of seconds to record the sweep angles for at each static point.
Some base stations are seen only a few times per second from certain spots;
shorter recordings would often leave them out.
"""

FREE_MOVEMENT_CHUNK_DURATION = 2.0
"""Number of seconds to record the sweep angles for in one go while the drone
is moving. Progress is reported after each chunk. The sweep angle stream of the
drone is turned off and on again between chunks.
"""

EXTRA_FREE_MOVEMENT_DURATION = 30.0
"""Number of seconds to record the sweep angles for when the samples of the
free movement are not sufficient.
"""

MIN_SAMPLES_PER_BASE_STATION = 20
"""Minimum number of samples that each base station must appear in."""

MIN_VISIBLE_BASE_STATIONS = 2
"""Minimum number of base stations that the drone must see at each static
point. A point that sees fewer base stations cannot be linked to the others.
"""

ABORTED_MESSAGE = "Calibration aborted by the user"


@dataclass(frozen=True)
class LighthouseGeometryEstimationOptions:
    """Options of the estimation of the geometry of the base stations."""

    x_axis_points: int = 2
    """Number of points on the positive X axis."""

    xy_plane_points: int = 2
    """Number of points on the floor, away from the X axis."""

    duration: float = 60.0
    """Number of seconds to record the sweep angles for while the user carries
    the drone around.
    """

    @classmethod
    def from_json(
        cls, params: Mapping[str, Any]
    ) -> LighthouseGeometryEstimationOptions:
        """Creates the options from the parameters of a calibration request.
        Missing parameters take their default values; other keys are ignored.

        Raises:
            RuntimeError: if a parameter is invalid
        """
        defaults = cls()
        return cls(
            x_axis_points=_get_int(params, "xAxisPoints", defaults.x_axis_points, 1),
            xy_plane_points=_get_int(
                params, "xyPlanePoints", defaults.xy_plane_points, 0
            ),
            duration=_get_positive_float(params, "duration", defaults.duration),
        )


class LighthouseGeometryEstimation:
    """Interactive estimation of the geometry of the base stations with a
    single drone.

    Call `run()` and iterate over the events that it yields. The result is
    available in `result` after the iteration finishes.
    """

    options: LighthouseGeometryEstimationOptions
    """Options of the estimation."""

    result: LighthouseConfiguration | None
    """The configuration to apply; ``None`` until the user accepts the
    estimated geometry.
    """

    _current: LighthouseConfiguration | None
    _uav: LighthouseUAV

    def __init__(
        self,
        uav: LighthouseUAV,
        options: LighthouseGeometryEstimationOptions,
        *,
        current: LighthouseConfiguration | None = None,
    ) -> None:
        """Constructor.

        Parameters:
            uav: the drone that measures the sweep angles
            options: options of the estimation
            current: the current configuration of the Lighthouse system; the
                estimated geometry is compared with it in the review step
        """
        self.options = options
        self.result = None
        self._current = current
        self._uav = uav

    async def run(self) -> ProgressEventsWithSuspension[None, Any]:
        """Performs the estimation.

        Yields `Suspend` objects at every step that needs the user. Resuming
        with a truthy value continues the estimation; a falsy value aborts it.

        Raises:
            NotSupportedError: if the drone does not support the Lighthouse
                system
            RuntimeError: if the user aborts the estimation or the geometry
                cannot be estimated
        """
        uav = self._uav
        options = self.options
        steps = [("origin", 1, 1)]
        steps += [
            ("x_axis", i + 1, options.x_axis_points)
            for i in range(options.x_axis_points)
        ]
        steps += [
            ("xy_plane", i + 1, options.xy_plane_points)
            for i in range(options.xy_plane_points)
        ]
        num_units = len(steps) + 2

        # Fail early if the drone has no Lighthouse support
        yield Progress(percentage=0, message=f"Checking UAV {uav.id}")
        await uav.get_lighthouse_configuration()

        static: list[Sample] = []
        for unit, (step, index, count) in enumerate(steps):
            warning: str | None = None
            while True:
                proceed = yield Suspend(
                    message=_get_instruction(step, index, count),
                    object=_step_info(step, index, count, warning),
                )
                if not proceed:
                    raise RuntimeError(ABORTED_MESSAGE)

                yield Progress(
                    percentage=_percentage(unit, num_units),
                    message="Recording sweep angles",
                )
                sample = await uav.collect_static_lighthouse_sample(
                    STATIC_SAMPLE_DURATION
                )
                if len(sample) >= MIN_VISIBLE_BASE_STATIONS:
                    break
                warning = _get_visibility_warning(sample)
            static.append(sample)

        free: list[Sample] = []
        duration = options.duration
        warning = None
        while True:
            proceed = yield Suspend(
                message=_get_free_movement_instruction(duration),
                object=_step_info("free", 1, 1, warning) | {"duration": duration},
            )
            if not proceed:
                raise RuntimeError(ABORTED_MESSAGE)

            elapsed = 0.0
            while elapsed < duration:
                yield Progress(
                    percentage=_percentage(len(steps) + elapsed / duration, num_units),
                    message=(
                        f"Recording sweep angles: {len(free)} samples, "
                        f"{duration - elapsed:.0f} s left"
                    ),
                )
                chunk = min(FREE_MOVEMENT_CHUNK_DURATION, duration - elapsed)
                free += await uav.collect_moving_lighthouse_samples(chunk)
                elapsed += chunk

            duration = EXTRA_FREE_MOVEMENT_DURATION
            warning = _get_coverage_warning(static + free)
            if warning:
                continue

            yield Progress(
                percentage=_percentage(len(steps) + 1, num_units),
                message="Estimating the geometry of the base stations",
            )
            samples = CalibrationSamples(
                static + free,
                num_x_axis_samples=options.x_axis_points,
                num_xy_plane_samples=options.xy_plane_points,
            )
            try:
                estimate = await to_thread.run_sync(estimate_geometry, samples)
            except BaseStationPlacementError as ex:
                warning = (
                    f"Could not place {_describe_base_stations(ex.base_stations)} "
                    f"relative to the others. Carry the drone where it sees "
                    f"{'them' if len(ex.base_stations) > 1 else 'it'} together "
                    f"with other base stations."
                )
            except RoomAlignmentError as ex:
                raise RuntimeError(
                    f"The points on the floor do not determine the coordinate "
                    f"system: {ex}. Restart the calibration and place the points "
                    f"on the X axis farther from the origin."
                ) from None
            else:
                break

        proceed = yield Suspend(
            message=(
                "Check the estimated geometry of the base stations. Continue to "
                "apply it to all drones, or abort to discard it."
            ),
            object=self._get_review_info(estimate, len(samples.samples)),
        )
        if not proceed:
            raise RuntimeError(ABORTED_MESSAGE)

        # Read the calibration data again; the drone may have received the
        # calibration data of more base stations since the start
        yield Progress(
            percentage=_percentage(num_units - 0.5, num_units),
            message=f"Reading calibration data from UAV {uav.id}",
        )
        latest = await uav.get_lighthouse_configuration()
        self.result = LighthouseConfiguration(
            system_type=latest.system_type,
            geometries=dict(estimate.geometries),
            calibrations=dict(latest.calibrations),
        )

    def _get_review_info(
        self, estimate: GeometryEstimate, num_samples: int
    ) -> dict[str, Any]:
        from .lighthouse import create_base_station_anchor

        result: dict[str, Any] = {
            "step": "review",
            "anchors": [
                create_base_station_anchor(index, geometry).json
                for index, geometry in sorted(estimate.geometries.items())
            ],
            "error": estimate.error,
            "converged": estimate.converged,
            "samples": num_samples,
        }

        current = self._current.geometries if self._current else {}
        comparison = {
            str(index + 1): _compare_geometries(geometry, current[index])
            for index, geometry in sorted(estimate.geometries.items())
            if index in current
        }
        if comparison:
            result["comparison"] = comparison

        return result


def _compare_geometries(
    new: LighthouseBsGeometry, old: LighthouseBsGeometry
) -> dict[str, float]:
    """Returns the distance between the origins of two geometries in metres
    and the angle between their rotations in degrees.
    """
    trace = sum(
        a * b
        for new_row, old_row in zip(new.rotation_matrix, old.rotation_matrix)
        for a, b in zip(new_row, old_row)
    )
    cos_angle = min(1.0, max(-1.0, (trace - 1) / 2))
    return {
        "distance": dist(new.origin, old.origin),
        "angle": degrees(acos(cos_angle)),
    }


def _describe_base_stations(indices: Iterable[int]) -> str:
    """Describes base stations given their zero-based indices, using the
    one-based numbers of their channels.
    """
    numbers = [str(index + 1) for index in sorted(indices)]
    if len(numbers) == 1:
        return f"base station {numbers[0]}"
    return f"base stations {', '.join(numbers[:-1])} and {numbers[-1]}"


def _get_coverage_warning(samples: list[Sample]) -> str | None:
    """Returns a warning if some base stations appear in too few samples, or
    ``None`` if every base station appears in enough samples.
    """
    counts: dict[int, int] = {}
    for sample in samples:
        for index in sample:
            counts[index] = counts.get(index, 0) + 1

    rare = [
        index for index, count in counts.items() if count < MIN_SAMPLES_PER_BASE_STATION
    ]
    if not rare:
        return None

    pronoun = "them" if len(rare) > 1 else "it"
    return (
        f"Too few samples from {_describe_base_stations(rare)}. Carry the drone "
        f"where it sees {pronoun} together with other base stations."
    )


def _get_free_movement_instruction(duration: float) -> str:
    return (
        f"Carry the drone around the area covered by the base stations for "
        f"{duration:.0f} seconds, at different heights, keeping it roughly level."
    )


def _get_instruction(step: str, index: int, count: int) -> str:
    if step == "origin":
        return (
            "Place the drone on the floor at the origin of the coordinate "
            "system, facing any direction."
        )

    suffix = f" Point {index} of {count}." if count > 1 else ""
    if step == "x_axis":
        return (
            "Place the drone on the floor on the positive X axis, at least "
            "0.5 m from the origin." + suffix
        )
    else:
        return "Place the drone on the floor away from the X axis." + suffix


def _get_int(params: Mapping[str, Any], key: str, default: int, minimum: int) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RuntimeError(f"{key} must be an integer not less than {minimum}")
    return value


def _get_positive_float(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeError(f"{key} must be a positive number")
    return float(value)


def _get_visibility_warning(sample: Sample) -> str:
    if sample:
        seen = f"Only {_describe_base_stations(sample)} was visible from here."
    else:
        seen = "No base station was visible from here."
    return (
        f"{seen} Move the drone to a spot where it sees at least "
        f"{MIN_VISIBLE_BASE_STATIONS} base stations."
    )


def _percentage(done: float, total: float) -> int:
    return int(100 * done / total)


def _step_info(
    step: str, index: int, count: int, warning: str | None
) -> dict[str, Any]:
    return {"step": step, "index": index, "count": count, "warning": warning}
