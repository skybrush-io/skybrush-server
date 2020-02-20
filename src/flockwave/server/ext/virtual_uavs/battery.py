"""Simulated battery for a virtual drone."""

from random import random
from typing import Optional

from flockwave.server.model.uav import BatteryInfo

__all__ = ("VirtualBattery",)


class VirtualBattery:
    """A virtual battery with voltage limits, linear discharge and a magical
    automatic recharge when it is about to be depleted.
    """

    def __init__(
        self,
        min_voltage: float = 9,
        max_voltage: float = 12.4,
        discharge_time: float = 600,
        initial_charge: Optional[float] = 1,
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
        """
        self._status = BatteryInfo()
        self._voltage_channel = None

        self._min = float(min_voltage)
        self._max = float(max_voltage)
        if self._max < self._min:
            self._min, self._max = self._max, self._min

        self._range = self._max - self._min

        self._peak_discharge_rate = self._range / discharge_time

        if initial_charge is not None:
            initial_charge = min(max(initial_charge, 0), 1)
        else:
            initial_charge = random() * 0.03 + 0.97

        self.voltage = initial_charge * self._range + self._min

    @property
    def status(self):
        """The general status of the battery as a BatteryInfo_ object."""
        return self._status

    @property
    def voltage(self):
        """The current voltage of the battery."""
        return self._status.voltage

    @voltage.setter
    def voltage(self, value):
        percentage = 100 * (value - self._min) / self._range
        self._status.voltage = value
        self._status.percentage = int(max(min(percentage, 100), 0))

    def recharge(self):
        """Recharges the battery to the maximum voltage."""
        self.voltage = self._max

    def discharge(self, dt, load, *, mutator=None):
        """Simulates the discharge of the battery over the given time
        period.

        Parameters:
            dt (float): the time that has passed
            load (float): the current workload of the system, expressed as a
                number between 0 (completely idle) and 1 (operating at full
                discharge rate)
        """
        new_voltage = self.voltage - dt * load * self._peak_discharge_rate
        while new_voltage < self._min:
            new_voltage += self._range
        self.voltage = new_voltage

        if mutator is not None:
            mutator.update(self._voltage_channel, self.voltage)

    def register_in_device_tree(self, node):
        """Registers the battery in the given device tree node of a UAV."""
        device = node.add_device("battery")
        self._voltage_channel = device.add_channel("voltage", type=float, unit="V")
