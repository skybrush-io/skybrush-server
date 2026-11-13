"""Extension that registers a dummy local positioning system (LPS) with
simulated anchors.

The dummy LPS is meant for testing and for developing user interfaces without
real hardware. It is registered when the extension is loaded and removed when
the extension is unloaded.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import atan2, cos, hypot, sin
from typing import TYPE_CHECKING, Any, TypedDict

from flockwave.gps.vectors import PositionXYZ
from flockwave.spec.errors import FlockwaveErrorCode
from trio import sleep

from flockwave.server.ext.lps import LocalPositioningSystemsExtensionAPI
from flockwave.server.ext.lps.model import (
    Anchor,
    LocalPositioningSystem,
    LocalPositioningSystemType,
)
from flockwave.server.model import Quaternion
from flockwave.server.model.commands import (
    Progress,
    ProgressEventsWithSuspension,
    Suspend,
)

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

__all__ = (
    "DummyLocalPositioningSystem",
    "DummyLocalPositioningSystemConfiguration",
    "DummyLocalPositioningSystemType",
)

DUMMY_LPS_ID = "dummy"
"""ID of the dummy local positioning system that the extension registers."""

DUMMY_LPS_TYPE_ID = "dummy"
"""ID of the type of the dummy local positioning system."""


class DummyLocalPositioningSystemConfiguration(TypedDict):
    """Configuration of the dummy local positioning system (LPS)."""

    width: float
    """Size of the room along the X axis, in meters."""

    depth: float
    """Size of the room along the Y axis, in meters."""

    height: float
    """Height of the anchors above the floor, in meters."""

    simulate_faults: bool
    """Whether to cycle one of the anchors through warning, error and inactive
    states periodically.
    """


DEFAULT_CONFIGURATION: DummyLocalPositioningSystemConfiguration = {
    "width": 4.0,
    "depth": 4.0,
    "height": 2.5,
    "simulate_faults": True,
}
"""Default configuration of the dummy local positioning system."""


def _update_anchor_to_ok(anchor: Anchor) -> None:
    anchor.activate()
    anchor.errors.clear()


def _update_anchor_to_warning(anchor: Anchor) -> None:
    anchor.activate()
    anchor.errors.clear()
    anchor.errors.ensure(FlockwaveErrorCode.UNSPECIFIED_WARNING)


def _update_anchor_to_error(anchor: Anchor) -> None:
    anchor.activate()
    anchor.errors.clear()
    anchor.errors.ensure(FlockwaveErrorCode.CONFIGURATION_ERROR)


def _update_anchor_to_inactive(anchor: Anchor) -> None:
    anchor.deactivate()
    anchor.errors.clear()


FAULT_CYCLE: tuple[Callable[[Anchor], None], ...] = (
    _update_anchor_to_ok,
    _update_anchor_to_warning,
    _update_anchor_to_error,
    _update_anchor_to_inactive,
)
"""Functions that update the faulty anchor of the dummy local positioning
system, in the order they are applied when faults are simulated.
"""


CALIBRATION_STEPS: tuple[str, ...] = (
    "Place the drone at the origin",
    "Place the drone on the positive X axis",
    "Place the drone in the XY plane, on the positive Y side",
)
"""Instructions for the user in each step of the simulated calibration."""

DEFAULT_SAMPLE_DURATION = 3.0
"""Default number of seconds to spend with collecting samples in each step of
the simulated calibration.
"""


def _get_sample_duration(params: Mapping[str, Any]) -> float:
    """Validates the calibration parameters of the dummy local positioning
    system and returns the sample duration.

    Raises:
        RuntimeError: if the calibration parameters are invalid
    """
    unknown = sorted(set(params) - {"sample_duration"})
    if unknown:
        raise RuntimeError(f"Unknown calibration parameters: {', '.join(unknown)}")

    value = params.get("sample_duration", DEFAULT_SAMPLE_DURATION)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeError("sample_duration must be a positive number")

    return float(value)


def _quaternion_facing(
    source: tuple[float, float, float], target: tuple[float, float, float]
) -> Quaternion:
    """Returns the attitude of an object at the given source point that faces
    the given target point.

    The X axis of the object points towards the target. The object is turned
    around the Z axis first (yaw) and then tilted around its own Y axis (pitch),
    with no rotation around its own X axis (roll).
    """
    dx, dy, dz = (t - s for s, t in zip(source, target))
    yaw = atan2(dy, dx)
    pitch = atan2(-dz, hypot(dx, dy))
    cy, sy = cos(yaw / 2), sin(yaw / 2)
    cp, sp = cos(pitch / 2), sin(pitch / 2)
    return (cy * cp, -sy * sp, cy * sp, sy * cp)


class DummyLocalPositioningSystem(
    LocalPositioningSystem[DummyLocalPositioningSystemConfiguration]
):
    """Dummy local positioning system (LPS) with simulated anchors.

    The LPS has four anchors at the top corners of a room. The center of the
    floor of the room is at the origin. Each anchor faces the origin, similarly
    to how Lighthouse base stations are mounted.

    This LPS instance is mostly for illustrative and testing purposes, and for
    developing user interfaces without real hardware.
    """

    fault_cycle_period: float = 5
    """Number of seconds that the faulty anchor spends in each state when
    faults are simulated.
    """

    _configuration: DummyLocalPositioningSystemConfiguration
    """The current configuration of the LPS."""

    def __init__(self) -> None:
        super().__init__()
        self._configuration = DEFAULT_CONFIGURATION.copy()
        self.anchors = [Anchor(id=str(index + 1)) for index in range(4)]
        self._update_anchor_geometry()

    async def calibrate(
        self, params: Mapping[str, Any]
    ) -> ProgressEventsWithSuspension[None, Any]:
        """Simulates a calibration that consists of multiple steps, similarly
        to the calibration of Lighthouse base stations.

        Each step suspends the calibration with an instruction for the user.
        Resuming the calibration with a truthy value collects samples for the
        current step. Resuming it with a falsy value aborts the calibration.

        Parameters:
            params: the calibration parameters. ``sample_duration`` is the
                number of seconds to collect samples for in each step;
                defaults to 3 seconds.
        """
        sample_duration = _get_sample_duration(params)
        num_steps = len(CALIBRATION_STEPS)

        for index, instruction in enumerate(CALIBRATION_STEPS):
            proceed = yield Suspend(
                message=instruction, object={"step": index + 1, "steps": num_steps}
            )
            if not proceed:
                raise RuntimeError("Calibration aborted by the user")

            # Report progress every second to keep the calibration alive
            elapsed = 0.0
            while elapsed < sample_duration:
                percentage = int(100 * (index + elapsed / sample_duration) / num_steps)
                yield Progress(percentage=percentage, message="Collecting samples")
                delay = min(1.0, sample_duration - elapsed)
                await sleep(delay)
                elapsed += delay

        yield Progress(percentage=100, message="Calibration finished")

    async def get_configuration(self) -> DummyLocalPositioningSystemConfiguration:
        return self._configuration.copy()

    async def run(self) -> None:
        """Simulates faults in the last anchor of the LPS if the configuration
        asks for it. Returns only when cancelled.
        """
        faulty_anchor = self.anchors[-1]
        index = 0
        while True:
            await sleep(self.fault_cycle_period)
            if self._configuration["simulate_faults"]:
                index = (index + 1) % len(FAULT_CYCLE)
            else:
                index = 0
            FAULT_CYCLE[index](faulty_anchor)
            self.notify_updated()

    async def _configure_inner(
        self, cfg: DummyLocalPositioningSystemConfiguration
    ) -> None:
        self._configuration = cfg.copy()
        self._update_anchor_geometry()
        self.notify_updated()

    def _update_anchor_geometry(self) -> None:
        """Updates the positions and attitudes of the anchors from the current
        configuration.
        """
        half_width = self._configuration["width"] / 2
        half_depth = self._configuration["depth"] / 2
        height = self._configuration["height"]
        corners = (
            (half_width, half_depth),
            (-half_width, half_depth),
            (-half_width, -half_depth),
            (half_width, -half_depth),
        )
        for anchor, (x, y) in zip(self.anchors, corners):
            anchor.position = PositionXYZ(x, y, height)
            anchor.attitude = _quaternion_facing((x, y, height), (0, 0, 0))


class DummyLocalPositioningSystemType(
    LocalPositioningSystemType[DummyLocalPositioningSystem]
):
    """Example local positioning system (LPS) type with simulated anchors.

    This LPS type is mostly for illustrative and testing purposes.
    """

    @property
    def description(self) -> str:
        return (
            "Local positioning system example with four simulated anchors "
            "that face the center of the room."
        )

    @property
    def name(self) -> str:
        return "Dummy LPS"

    def create(self) -> DummyLocalPositioningSystem:
        return DummyLocalPositioningSystem()

    def get_configuration_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "width": {
                    "type": "number",
                    "title": "Width",
                    "description": "Size of the room along the X axis, in meters",
                    "exclusiveMinimum": 0,
                    "default": DEFAULT_CONFIGURATION["width"],
                },
                "depth": {
                    "type": "number",
                    "title": "Depth",
                    "description": "Size of the room along the Y axis, in meters",
                    "exclusiveMinimum": 0,
                    "default": DEFAULT_CONFIGURATION["depth"],
                },
                "height": {
                    "type": "number",
                    "title": "Height",
                    "description": "Height of the anchors above the floor, in meters",
                    "exclusiveMinimum": 0,
                    "default": DEFAULT_CONFIGURATION["height"],
                },
                "simulate_faults": {
                    "type": "boolean",
                    "title": "Simulate faults",
                    "description": (
                        "Cycle one of the anchors through warning, error and "
                        "inactive states periodically"
                    ),
                    "default": DEFAULT_CONFIGURATION["simulate_faults"],
                },
            },
            "required": ["width", "depth", "height", "simulate_faults"],
            "additionalProperties": False,
        }


async def run(app: SkybrushServer) -> None:
    """Runs the extension."""
    lps_api = app.import_api("lps", LocalPositioningSystemsExtensionAPI)
    with (
        lps_api.use_lps_type(DUMMY_LPS_TYPE_ID, DummyLocalPositioningSystemType()),
        lps_api.create_and_use_lps(DUMMY_LPS_TYPE_ID, id=DUMMY_LPS_ID) as lps,
    ):
        assert isinstance(lps, DummyLocalPositioningSystem)
        await lps.run()


dependencies = ("lps",)
description = "Dummy local positioning system with simulated anchors, for testing"
schema = {}
tags = ("experimental", "indoor")
