"""Classes and functions related to the on-board accelerometer calibration
procedure on ArduPilot UAVs.
"""

from enum import IntEnum
from math import inf

from flockwave.server.model.commands import ProgressEventsWithSuspension
from flockwave.server.tasks import ProgressReporter

from .enums import AccelCalVehiclePos, MAVCommand
from .types import MAVLinkMessage

__all__ = ("AccelerometerCalibration",)


class AccelerometerCalibrationStatus(IntEnum):
    """Enum representing the possible states of the state machine during an
    accelerometer calibration process.
    """

    NOT_RUNNING = 0
    CALIBRATING = 1
    SUCCESSFUL = 3
    FAILED = 4


class AccelerometerCalibration:
    """Object encapsulating the state of the accelerometer calibration on an
    ArduPilot UAV with possibly multiple accelerometers.
    """

    _next_step: AccelCalVehiclePos
    """Step of the calibration process; in other words, the side on which the
    UAV has to be placed next _if_ we are waiting for an action from the
    operator.
    """

    _status: AccelerometerCalibrationStatus
    """Status of the calibration process (not running, calibrating, successful
    or failed).
    """

    _percentage: int
    """Progress of the calibration, expressed as a percentage."""

    _reporter: ProgressReporter[None, str]
    """Progress reporter object corresponding to the calibration."""

    def __init__(self):
        """Constructor."""
        self.reset()

    @property
    def failed(self) -> bool:
        """Returns whether the accelerometer calibration failed."""
        return self._status is AccelerometerCalibrationStatus.FAILED

    @property
    def next_step(self) -> AccelCalVehiclePos:
        return self._next_step

    @property
    def running(self) -> bool:
        """Returns whether the accelerometer calibration is running."""
        return self._status is AccelerometerCalibrationStatus.CALIBRATING

    @property
    def successful(self) -> bool:
        """Returns whether the accelerometer calibration was successful."""
        return self._status is AccelerometerCalibrationStatus.SUCCESSFUL

    @property
    def terminated(self) -> bool:
        """Returns whether the accelerometer calibration terminated."""
        return self.failed or self.successful

    def notify_resumed(self) -> None:
        """Notifies the accelerometer calibration process that it has been
        resumed successfully.
        """
        self._reporter.notify(message="Please wait, calibrating...")

    def reset(self) -> None:
        """Resets the state of the accelerometer calibration state variable."""
        self._next_step = AccelCalVehiclePos.NOT_STARTED
        self._status = AccelerometerCalibrationStatus.NOT_RUNNING
        self._percentage = 0
        self._reporter = ProgressReporter(auto_close=True)

    def updates(
        self, timeout: float = inf, fail_on_timeout: bool = True
    ) -> ProgressEventsWithSuspension[None, str]:
        """Returns an async iterator generating progress messages from the current
        calibration task.
        """
        return self._reporter.updates(timeout=timeout, fail_on_timeout=fail_on_timeout)

    def handle_message_accelcal_vehicle_pos(self, message: MAVLinkMessage):
        """Handles a MAV_CMD_ACCELCAL_VEHICLE_POS message from the autopilot."""

        if message.command != MAVCommand.ACCELCAL_VEHICLE_POS:
            raise ValueError(
                f"Command type mismatch: {message.command} != "
                f"{MAVCommand.ACCELCAL_VEHICLE_POS}"
            )

        try:
            step = AccelCalVehiclePos(message.param1)
        except ValueError:
            raise ValueError(
                f"Invalid accelerometer calibration vehicle position value: "
                f"{message.param1}"
            ) from None

        self._next_step = step

        # store new status and progress
        suspend = False
        if step.is_waiting_for_action:
            self._percentage = int(step) * 15 - 5  # 10, 25, 40, 55, 70, 85
            self._status = AccelerometerCalibrationStatus.CALIBRATING
            suspend = True
            reply = self._next_step.as_action()
        elif step.is_successful:
            self._percentage = 100
            self._status = AccelerometerCalibrationStatus.SUCCESSFUL
            reply = "Calibration successful"
        elif step.is_failure:
            # Keep the percentage where it was so we know how much of the
            # calibration went through before it failed
            self._status = AccelerometerCalibrationStatus.FAILED
            reply = "Calibration failed"
        else:
            self._percentage = 0
            self._status = AccelerometerCalibrationStatus.NOT_RUNNING
            reply = ""

        # report progress of the calibration
        if not self._reporter.done:
            if self.failed:
                self._reporter.fail(reply)
            elif suspend:
                self._reporter.notify(self._percentage)
                self._reporter.suspend(reply)
            else:
                self._reporter.notify(self._percentage, reply)
