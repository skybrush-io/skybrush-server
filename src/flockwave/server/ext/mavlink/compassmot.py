"""Classes and functions related to the on-board compass calibration
procedure on ArduPilot UAVs.
"""

from collections import defaultdict
from dataclasses import dataclass
from enum import IntEnum
from math import inf
from time import monotonic
from typing import AsyncIterator, Protocol, Sequence, TypeAlias

from trio_util import periodic

from flockwave.server.model.commands import ProgressEventsWithSuspension
from flockwave.server.tasks import ProgressReporter

from .types import MAVLinkMessage

__all__ = ("CompassMotorInterferenceCalibration",)


class CompassMotorInterferenceCalibrationStatus(IntEnum):
    NOT_RUNNING = 0
    CALIBRATING = 1
    SUCCESSFUL = 2
    FAILED = 3


@dataclass
class ThrottleAction:
    value: float
    should_sample: bool = True
    done: bool = False


class ThrottleSchedule(Protocol):
    """Type alias for an object that maps the elapsed time since the start of the
    compass-motor interference calibration to a Throttle_ object indicating the
    throttle value to be applied to the UAV.
    """

    def get_action(self, dt: float) -> ThrottleAction: ...
    def get_total_duration(self) -> float | None: ...


ScalarSample: TypeAlias = tuple[float, float]
"""Type alias for scalar samples collected during the calibration process."""

VectorSample: TypeAlias = tuple[float, tuple[float, float, float]]
"""Type alias for vector samples collected during the calibration process."""


@dataclass
class CompassMotorInterferenceCalibrationResult:
    x: float
    y: float
    z: float
    instance: int = 0

    def to_parameter_dict(self) -> dict[str, float]:
        instance = self.instance
        prefix = "COMPASS_MOT" if instance == 0 else f"COMPASS_MOT{instance + 1}"
        return {
            f"{prefix}_X": self.x,
            f"{prefix}_Y": self.y,
            f"{prefix}_Z": self.z,
        }


class CompassMotorInterferenceCalibration:
    """Object encapsulating the state of the compass-motor interference calibration on
    an ArduPilot UAV with possibly multiple compasses.
    """

    _status: CompassMotorInterferenceCalibrationStatus
    """Status of the calibration process."""

    _reporter: ProgressReporter[None, str]
    """Progress reporter object corresponding to the calibration."""

    _current_samples: list[ScalarSample] = []
    """Current samples collected during the calibration process, with timestamps."""

    _mag_samples_by_instance: defaultdict[int, list[VectorSample]] = defaultdict(list)
    """Magnetometer raw measurement samples collected during the calibration process,
    with timestamps.
    """

    def __init__(self):
        """Constructor."""
        # Stores the calibration status of each compass
        self._status = CompassMotorInterferenceCalibrationStatus.NOT_RUNNING
        self.reset()

    def reset(self):
        """Resets the state of the compass-motor interference calibration state variable."""
        self._status = CompassMotorInterferenceCalibrationStatus.NOT_RUNNING
        self._reporter = ProgressReporter(auto_close=True)
        self._current_samples.clear()
        self._mag_samples_by_instance.clear()

    @property
    def failed(self) -> bool:
        """Returns whether the compass-motor interference calibration failed."""
        return self._status is CompassMotorInterferenceCalibrationStatus.FAILED

    @property
    def running(self) -> bool:
        """Returns whether the compass-motor interference calibration is running.

        The compass calibration is running if there is at least one compass
        that is being calibrated currently.
        """
        return self._status is CompassMotorInterferenceCalibrationStatus.CALIBRATING

    @property
    def successful(self) -> bool:
        """Returns whether the compass-motor interference calibration was successful."""
        return self._status is CompassMotorInterferenceCalibrationStatus.SUCCESSFUL

    @property
    def terminated(self) -> bool:
        """Returns whether the compass-motor interference calibration terminated."""
        return self.successful or self.failed

    def handle_message_battery_status(self, message: MAVLinkMessage) -> None:
        """Handles a BATTERY_STATUS message from the autopilot."""
        sample = message.current_battery / 100.0  # centiamps to amps
        now = monotonic()
        self._current_samples.append((now, sample))

    def handle_message_scaled_imu(self, message: MAVLinkMessage) -> None:
        """Handles SCALED_IMU, SCALED_IMU2 and SCALED_IMU3 messages from the autopilot."""
        match message.get_type():
            case "SCALED_IMU":
                instance = 0
            case "SCALED_IMU2":
                instance = 1
            case "SCALED_IMU3":
                instance = 2
            case _:
                return

        sample = (message.xmag, message.ymag, message.zmag)
        now = monotonic()
        self._mag_samples_by_instance[instance].append((now, sample))

    async def actions(
        self, schedule: ThrottleSchedule | None = None, *, update_rate_hz: int = 5
    ) -> AsyncIterator[ThrottleAction]:
        """Returns an async iterator generating throttle actions to be performed on the
        UAV during the compass-motor interference calibration.
        """
        schedule = schedule or LinearThrottleRamp(
            ramp_time=10, pre_delay=2, post_delay=1
        )
        dt = 1 / update_rate_hz
        total = schedule.get_total_duration()

        try:
            async for elapsed, _ in periodic(dt):
                action = schedule.get_action(elapsed)
                yield action

                if total is not None:
                    self._reporter.notify(int(elapsed * 100 / total))

                if action.done:
                    break

        finally:
            self._reporter.close()

    def updates(
        self, timeout: float = inf, fail_on_timeout: bool = True
    ) -> ProgressEventsWithSuspension[None, str]:
        """Returns an async iterator generating progress messages from the current
        calibration task.
        """
        return self._reporter.updates(timeout=timeout, fail_on_timeout=fail_on_timeout)

    def calculate_calibration_parameters(
        self, *, min_current: float = 0
    ) -> dict[str, float]:
        """Calculates the calibration parameters from the collected samples.

        Args:
            min_current: minimum current to use for the fitting process
        """
        result: dict[str, float] = {}

        for instance, mag_samples in sorted(self._mag_samples_by_instance.items()):
            if not result:
                result["COMPASS_MOTCT"] = 2

            params = perform_compass_motor_interference_calibration(
                self._current_samples,
                mag_samples,
                instance=instance,
                min_source=min_current,
            )
            result.update(params.to_parameter_dict())

        return result


class LinearThrottleRamp:
    """Class implementing a simple linear throttle ramp schedule for the compass-motor
    interference calibration.
    """

    cycles: int
    min_throttle: float
    max_throttle: float
    post_delay: float
    pre_delay: float
    ramp_time: float

    def __init__(
        self,
        *,
        ramp_time: float,
        min_throttle: float = 0,
        max_throttle: float = 1,
        cycles: int = 1,
        pre_delay: float = 0,
        post_delay: float = 0,
    ):
        self.min_throttle = min_throttle
        self.max_throttle = max_throttle
        self.ramp_time = ramp_time
        self.cycles = cycles
        self.pre_delay = pre_delay
        self.post_delay = post_delay

    def get_action(self, dt: float) -> ThrottleAction:
        """Returns the throttle action corresponding to the given elapsed time since
        the start of the compass-motor interference calibration.

        Args:
            dt: the time elapsed, in seconds
        """
        cycle_time = self.ramp_time * 2
        total_cycle_time = cycle_time * self.cycles
        total_time = self.get_total_duration()

        if dt < self.pre_delay:
            return ThrottleAction(self.min_throttle, False, False)

        if dt > total_time:
            return ThrottleAction(self.min_throttle, False, True)

        dt -= self.pre_delay
        if dt > total_cycle_time:
            return ThrottleAction(self.min_throttle)

        cycle_position = dt % cycle_time
        if cycle_position < self.ramp_time:
            value = self.min_throttle + (self.max_throttle - self.min_throttle) * (
                cycle_position / self.ramp_time
            )
        else:
            value = self.max_throttle - (self.max_throttle - self.min_throttle) * (
                (cycle_position - self.ramp_time) / self.ramp_time
            )

        return ThrottleAction(value)

    def get_total_duration(self) -> float:
        """Returns the total duration of the linear throttle ramp schedule."""
        return self.pre_delay + self.ramp_time * 2 * self.cycles + self.post_delay


def perform_compass_motor_interference_calibration(
    source: Sequence[ScalarSample],
    mag_samples: Sequence[VectorSample],
    *,
    instance: int = 0,
    min_source: float | None = None,
    min_samples: int = 50,
) -> CompassMotorInterferenceCalibrationResult:
    import numpy as np

    timestamps = np.array([t for t, _ in source])
    current_data = np.array([v for _, v in source])

    mag_timestamps = np.array([t for t, _ in mag_samples])
    mag_data = np.array([v for _, v in mag_samples])

    interp_mag_data = np.zeros((timestamps.shape[0], 3))
    for i in range(3):
        interp_mag_data[:, i] = np.interp(timestamps, mag_timestamps, mag_data[:, i])

    selected = current_data >= min_source
    if selected.sum() < min_samples:
        raise RuntimeError(
            "Not enough samples were collected during the calibration procedure. "
            "Raise the max throttle and check the calibration of the current "
            "readings from the battery."
        )

    slope, offset = np.polyfit(current_data[selected], interp_mag_data[selected], 1)
    x, y, z = -slope

    return CompassMotorInterferenceCalibrationResult(
        float(x), float(y), float(z), instance
    )
