"""Classes and functions related to the on-board compass calibration
procedure on ArduPilot UAVs.
"""

from dataclasses import dataclass
from enum import IntEnum
from math import inf
from typing import AsyncIterator, Protocol

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
    value: int
    should_sample: bool = True
    done: bool = False


class ThrottleSchedule(Protocol):
    """Type alias for an object that maps the elapsed time since the start of the
    compass-motor interference calibration to a Throttle_ object indicating the
    throttle value to be applied to the UAV.
    """

    def get_action(self, dt: float) -> ThrottleAction: ...
    def get_total_duration(self) -> float | None: ...


class CompassMotorInterferenceCalibration:
    """Object encapsulating the state of the compass-motor interference calibration on
    an ArduPilot UAV with possibly multiple compasses.
    """

    _status: CompassMotorInterferenceCalibrationStatus
    """Status of the calibration process."""

    _reporter: ProgressReporter[None, str]
    """Progress reporter object corresponding to the calibration."""

    def __init__(self):
        """Constructor."""
        # Stores the calibration status of each compass
        self._status = CompassMotorInterferenceCalibrationStatus.NOT_RUNNING
        self.reset()

    def reset(self):
        """Resets the state of the compass-motor interference calibration state variable."""
        self._status = CompassMotorInterferenceCalibrationStatus.NOT_RUNNING
        self._reporter = ProgressReporter(auto_close=True)

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
        # TODO(ntamas)
        pass

    def handle_message_scaled_imu(self, message: MAVLinkMessage) -> None:
        """Handles SCALED_IMU, SCALED_IMU2 and SCALED_IMU3 messages from the autopilot."""
        # TODO(ntamas)
        pass

    async def actions(
        self, schedule: ThrottleSchedule | None = None, *, update_rate_hz: int = 5
    ) -> AsyncIterator[ThrottleAction]:
        """Returns an async iterator generating throttle actions to be performed on the
        UAV during the compass-motor interference calibration.
        """
        schedule = schedule or LinearThrottleRamp(ramp_time=10)
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


class LinearThrottleRamp:
    """Class implementing a simple linear throttle ramp schedule for the compass-motor
    interference calibration.
    """

    def __init__(
        self,
        *,
        ramp_time: float,
        min_throttle: int = 1000,
        max_throttle: int = 2000,
        cycles: int = 1,
        initial_delay: float = 0,
    ):
        self.min_throttle = min_throttle
        self.max_throttle = max_throttle
        self.ramp_time = ramp_time
        self.cycles = cycles
        self.initial_delay = initial_delay

    def get_action(self, dt: float) -> ThrottleAction:
        """Returns the throttle action corresponding to the given elapsed time since
        the start of the compass-motor interference calibration.

        Args:
            dt: the time elapsed, in seconds
        """
        cycle_time = self.ramp_time * 2
        total_time = cycle_time * self.cycles

        dt -= self.initial_delay

        if dt < 0:
            return ThrottleAction(self.min_throttle, False, False)

        if dt > total_time:
            return ThrottleAction(self.min_throttle, False, True)

        cycle_position = dt % cycle_time
        if cycle_position < self.ramp_time:
            value = int(
                self.min_throttle
                + (self.max_throttle - self.min_throttle)
                * (cycle_position / self.ramp_time)
            )
        else:
            value = int(
                self.max_throttle
                - (self.max_throttle - self.min_throttle)
                * ((cycle_position - self.ramp_time) / self.ramp_time)
            )

        return ThrottleAction(value)

    def get_total_duration(self) -> float:
        """Returns the total duration of the linear throttle ramp schedule."""
        return self.initial_delay + self.ramp_time * 2 * self.cycles
