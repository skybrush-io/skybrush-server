"""Classes corresponding to the time axis management of drone shows."""

from abc import ABC, abstractmethod
from logging import Logger
from typing import Generic, TypeAlias, TypeVar

from trio import TASK_STATUS_IGNORED, current_time, sleep, sleep_until
from trio.lowlevel import ParkingLot

from flockwave.server.model import UAV

__all__ = (
    "BinaryTimeAxisConfiguration",
    "TimeAxisConfigurationManager",
)


BinaryTimeAxisConfiguration: TypeAlias = bytes
"""Binary representation of a time axis configuration
to be sent to drones."""

TUAV = TypeVar("TUAV", bound="UAV")


class TimeAxisConfigurationManager(ABC, Generic[TUAV]):
    """Class that manages the time axis configuration process on a group of drones.

    The class provides the following facilities:

    - maintains a current time axis configuration
    - broadcasts messages containing the current time axis configuration
      periodically (typically at 0.2 Hz for a few secs after configuration
      change and at 1s later)

    This class is meant to serve as a base class for concrete implementations
    in the context of another extension. For instance, in the `mavlink`
    extension, each MAVLink network has an instance of this object that manages
    the time axis configuration updates for that given MAVLink network.
    """

    _config: BinaryTimeAxisConfiguration | None = None
    """The binary representation of the time axis configuration of the show,
    including causal time axis segments with different rates of time.
    """

    _log: Logger | None = None
    """The logger that the time axis configuration manager uses to log events."""

    _parking_lot: ParkingLot
    """Low-level task coordination primitive that manages the execution of
    background tasks performed by this object.
    """

    _config_last_updated_at: float = 0
    """Timestamp when the time axis configuration got updated the last time."""

    broadcast_frequency: float = 1
    """The regular broadcast frequency of the time axis configuration,
    in seconds."""

    duration_of_rapid_broadcast: float = 5
    """The time period after a time axis configuration update while the
    broadcast frequency is raised, in seconds."""

    rapid_broadcast_frequency: float = 0.2
    """The broadcast frequency of the time axis configuration right after
    a config update, in seconds."""

    def __init__(
        self,
        *,
        log: Logger | None = None,
    ):
        """Constructor.

        Parameters:
            log: the logger to use to log messages from this object
        """
        self._log = log

        self._parking_lot = ParkingLot()

    @abstractmethod
    async def broadcast_time_axis_configuration(
        self, config: BinaryTimeAxisConfiguration
    ) -> None:
        """Broadcasts a message that configures the time axis configuration for all UAVs.

        May be a no-op if broadcasts are not supported; in this case the manager
        will fall back to individual configuration requests.

        Exceptions from this method are caught and ignored by the manager.
        If you want to log them, add your own logging in the implementation of
        this method.
        """
        ...

    @property
    def config(self) -> BinaryTimeAxisConfiguration | None:
        return self._config

    def notify_time_axis_config_changed(
        self, config: BinaryTimeAxisConfiguration | None
    ) -> None:
        """Notifies the manager that the time axis configuration has
        changed.
        """
        self._config = config
        self._config_last_updated_at = current_time()
        if config is not None:
            self._parking_lot.unpark_all()

    async def run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        """Background task that checks the time axis configuration on the UAVs
        of this network regularly and updates them as needed.
        """
        while True:
            try:
                await self._run(task_status=task_status)
            except Exception:
                if self._log:
                    self._log.exception(
                        "Time axis configuration manager stopped unexpectedly, restarting..."
                    )

                # Ensure that we call task_status.started() only once
                task_status = TASK_STATUS_IGNORED
                await sleep(0.5)

    async def _run(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        task_status.started()

        interval: float = self.broadcast_frequency

        while True:
            config = self._config
            if config is None:
                # No time axis config yet, wait for one
                await self._parking_lot.park()
                continue

            now = current_time()

            # Broadcast a packet that contains the desired time axis
            # configuration. If it fails, well, it does not matter,
            # we will broadcast it again
            try:
                await self.broadcast_time_axis_configuration(config)
            except Exception:
                # Do not blow up if the broadcasting fails for any reason,
                # but print a warning
                if self._log is not None:
                    self._log.warning("Broadcast of time axis configuration failed")

            # set frequency of broadcast update based on how fresh the
            # latest confguration change is
            if now - self._config_last_updated_at < self.duration_of_rapid_broadcast:
                interval = self.rapid_broadcast_frequency
            else:
                interval = self.broadcast_frequency

            await sleep_until(now + interval)
