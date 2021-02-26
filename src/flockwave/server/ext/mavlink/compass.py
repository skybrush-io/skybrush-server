"""Classes and functions related to the on-board compass calibration
procedure on ArduPilot UAVs.
"""

from enum import IntEnum
from trio import sleep

from .enums import MagCalStatus
from .types import MAVLinkMessage

__all__ = ("CompassCalibration",)


class CompassCalibrationStatus(IntEnum):
    NOT_RUNNING = 0
    CALIBRATING = 1
    SUCCESSFUL = 2
    FAILED = 3


class CompassCalibration:
    """Object encapsulating the state of the compass calibration on an
    ArduPilot UAV.
    """

    def __init__(self):
        """Constructor."""
        # Stores the calibration status of each compass
        self._status = []
        self._percentages = []
        self.reset()

    def reset(self):
        """Resets the state of the compass calibration state variable."""
        self._status.clear()
        self._percentages.clear()

    @property
    def failed(self) -> bool:
        """Returns whether the compass calibration failed.

        A compass calibration failed if at least one compass was calibrated,
        none of the compasses are being calibrated currently and at least one
        of the compasses failed to calibrate.
        """
        return self.terminated and any(
            status is CompassCalibrationStatus.FAILED for status in self._status
        )

    @property
    def running(self) -> bool:
        """Returns whether the compass calibration is running.

        The compass calibration is running if there is at least one compass
        that is being calibrated currently.
        """
        return any(
            status is CompassCalibrationStatus.CALIBRATING for status in self._status
        )

    @property
    def successful(self) -> bool:
        """Returns whether the compass calibration was successful.

        A compass calibration is successful if at least one compass was
        calibrated, none of the compasses are being calibrated currently and
        all the compasses that were calibrated were calibrated successfully.
        """
        return self.terminated and all(
            status is CompassCalibrationStatus.SUCCESSFUL for status in self._status
        )

    @property
    def terminated(self) -> bool:
        """Returns whether the compass calibration terminated.

        A compass calibration terminated if at least one compass was calibrated
        and none of the compasses are being calibrated currently.
        """
        return len(self._status) > 0 and not self.running

    def handle_message_mag_cal_progress(self, message: MAVLinkMessage):
        """Handles a MAG_CAL_PROGRESS message from the autopilot."""
        return self._handle_mag_cal_message(message)

    def handle_message_mag_cal_report(self, message: MAVLinkMessage):
        """Handles a MAG_CAL_REPORT message from the autopilot."""
        return self._handle_mag_cal_message(message)

    def _handle_mag_cal_message(self, message: MAVLinkMessage):
        """Common implementation for the handling of MAG_CAL_PROGRESS and
        MAG_CAL_REPORT messages.
        """
        compass_id = message.compass_id
        cal_mask = message.cal_mask
        cal_status = MagCalStatus(message.cal_status)

        self._ensure_num_compasses_at_least(max(compass_id + 1, cal_mask.bit_length()))
        self._fill_compass_statuses_from_calibration_mask(cal_mask)

        if cal_status.is_calibrating:
            if hasattr(message, "completion_pct"):
                self._percentages[compass_id] = message.completion_pct
            self._status[compass_id] = CompassCalibrationStatus.CALIBRATING
        elif cal_status.is_successful:
            self._percentages[compass_id] = 100
            self._status[compass_id] = CompassCalibrationStatus.SUCCESSFUL
        elif cal_status.is_failure:
            self._percentages[compass_id] = 0
            self._status[compass_id] = CompassCalibrationStatus.NOT_RUNNING
        else:
            # Keep the percentage where it was so we know how much of the
            # calibration went through before it failed
            self._status[compass_id] = CompassCalibrationStatus.FAILED

    async def wait_until_termination(self) -> bool:
        """Waits until the messages from the autopilot indicate that the
        compass calibration has finished.

        Returns:
            whether the compass calibration was successful
        """
        # First wait until the calibration starts
        while not self.running:
            await sleep(0.5)

        # Now wait until the calibration terminates
        while not self.terminated:
            await sleep(0.5)

        # Return whether the calibration was successful
        return self.successful

    def _ensure_num_compasses_at_least(self, num_compasses: int) -> None:
        """Ensures that the internal data structures of the compass have
        space that is enough to store the given number of compasses.
        """
        diff = num_compasses - len(self._status)
        if diff > 0:
            self._percentages.extend([0] * diff)
            self._status.extend([CompassCalibrationStatus.NOT_RUNNING] * diff)

    def _fill_compass_statuses_from_calibration_mask(self, cal_mask: int) -> None:
        """Updates the compass statuses based on a "calibration mask", i.e. a
        bit field that indicates which compasses are being calibrated right now.
        """
        if cal_mask < 0:
            return

        for i in range(cal_mask.bit_length()):
            if (
                cal_mask & (1 << i)
                and self._status[i] == CompassCalibrationStatus.NOT_RUNNING
            ):
                self._status[i] = CompassCalibrationStatus.CALIBRATING
