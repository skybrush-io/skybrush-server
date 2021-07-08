"""Simulated battery for a virtual drone."""

from random import random
from typing import Optional

from flockwave.server.model.battery import BatteryInfo
from flockwave.server.utils import clamp

__all__ = ("VirtualBattery",)


class VirtualBattery:
    """A virtual battery with voltage limits, linear discharge and a magical
    automatic recharge when it is about to be depleted.
    """

    _report_percentage: bool
    _status: BatteryInfo
    _voltage: float

    def __init__(
        self,
        min_voltage: float = 9,
        max_voltage: float = 12.6,
        discharge_time: float = 600,
        initial_charge: Optional[float] = 1,
        report_percentage: bool = True,
    ):
        """Constructor.

        Parameters:
            min_voltage (float): the minimum voltage of the battery when it
                will magically recharge
            max_voltage (float): the maximum voltage of the battery
            discharge_time (float): number of seconds after which the battery
                becomes discharged when fully loaded
            initial_charge (Optional[float]): initial relative charge of the
                battery, expressed as a float between 0 (completely empty) and
                1 (completely full). `None` means to simulate a nearly-full
                charge with some small variation.
            report_percentage: whether the virtual battery reports its charge
                percentage as well as its voltage
        """
        self._report_percentage = bool(report_percentage)
        self._status = BatteryInfo()
        self._voltage_channel = None

        self._min = float(min_voltage)
        self._max = float(max_voltage)
        if self._max < self._min:
            self._min, self._max = self._max, self._min

        self._range = self._max - self._min

        self._peak_discharge_rate = self._range / discharge_time

        if initial_charge is not None:
            initial_charge = clamp(initial_charge, 0.0, 1.0)
        else:
            initial_charge = random() * 0.03 + 0.97

        self._critical_threshold = self._min + self._range * 0.1
        self._very_low_threshold = self._min + self._range * 0.25
        self._low_threshold = self._min + self._range / 3

        self.voltage = initial_charge * self._range + self._min

    @property
    def is_critical(self) -> bool:
        """Returns whether the battery voltage is considered critically."""
        voltage = self._status.voltage
        return voltage is not None and voltage <= self._critical_threshold

    @property
    def is_low(self) -> bool:
        """Returns whether the battery voltage is considered low."""
        voltage = self._status.voltage
        return voltage is not None and voltage <= self._low_threshold

    @property
    def is_very_low(self) -> bool:
        """Returns whether the battery voltage is considered very low."""
        voltage = self._status.voltage
        return voltage is not None and voltage <= self._very_low_threshold

    @property
    def percentage(self) -> Optional[int]:
        """The current charge percentage of the battery, in the range of 0 to
        100, or `None` if the battery does not report percentages.
        """
        return self._status.percentage

    @percentage.setter
    def percentage(self, value):
        value = clamp(value, 0, 100)
        self.voltage = self._min + self._range * (value / 100)

    @property
    def status(self) -> BatteryInfo:
        """The general status of the battery as a BatteryInfo_ object."""
        return self._status

    @property
    def voltage(self) -> Optional[float]:
        """The current voltage of the battery."""
        return self._status.voltage

    @voltage.setter
    def voltage(self, value):
        self._status.voltage = value

        if self._report_percentage:
            percentage = (value - self._min) / self._range * 100
            self._status.percentage = round(clamp(percentage, 0, 100))

    def recharge(self) -> None:
        """Recharges the battery to the maximum voltage."""
        self.voltage = self._max

    def discharge(self, dt: float, load: float, *, mutator=None):
        """Simulates the discharge of the battery over the given time
        period.

        Parameters:
            dt: the time that has passed
            load: the current workload of the system, expressed as a number
                between 0 (completely idle) and 1 (operating at full discharge
                rate)
        """
        voltage = self.voltage
        discharged = dt * load * self._peak_discharge_rate
        new_voltage = (voltage if voltage is not None else self._max) - discharged
        while new_voltage < self._min:
            new_voltage += self._range
        self.voltage = new_voltage

        if mutator is not None:
            mutator.update(self._voltage_channel, self.voltage)

    def register_in_device_tree(self, node):
        """Registers the battery in the given device tree node of a UAV."""
        device = node.add_device("battery")
        self._voltage_channel = device.add_channel("voltage", type=float, unit="V")
