"""Background tasks managed by the mission extension."""

from contextlib import ExitStack, contextmanager
from logging import Logger
from trio import sleep
from typing import cast, Any, Dict, Iterator, Optional

from flockwave.concurrency.scheduler import Job, Scheduler

from flockwave.server.utils import overridden
from flockwave.server.utils.formatting import format_timestamp_nicely

from .model import Mission
from .registry import MissionRegistry

__all__ = ("MissionSchedulerTask",)


class MissionSchedulerTask:
    """Scheduler task that watches missions in the mission registry and
    starts them when their scheduled start time has come.
    """

    log: Optional[Logger] = None
    """Logger that the task will log events to."""

    mission_registry: MissionRegistry
    """The registry containing the missions being managed."""

    scheduler: Optional[Scheduler] = None
    """The scheduler that is responsible for starting tasks related to missions."""

    _missions_to_jobs: Dict[Mission, Job]
    """Dictionary mapping the scheduled missions to the corresponding job objects
    in the scheduler.
    """

    def __init__(self, mission_registry: MissionRegistry):
        self._mission_registry = mission_registry
        self._missions_to_jobs = {}

    def cancel_mission(self, mission: Mission) -> None:
        """Cancels the execution of the given mission if it is running. No-op
        if the mission is not running.

        Parameters:
            mission: the mission to cancel
        """
        job = self._missions_to_jobs.get(mission)
        if job is not None:
            if self.scheduler:
                self.scheduler.cancel(job)
            del self._missions_to_jobs[mission]

    async def run(self, log: Optional[Logger] = None):
        with ExitStack() as stack:
            stack.enter_context(overridden(self, log=log, scheduler=Scheduler()))
            stack.enter_context(self._subscribed_to_missions())

            assert self.scheduler is not None
            await self.scheduler.run()

    async def _run_mission(self, mission: Mission) -> None:
        """Runs the task related to the given mission. This is the function
        that is scheduled in the scheduler to the start time of the mission.
        """
        if self.log:
            self.log.info("Starting mission", extra={"id": mission.id})

        # TODO(ntamas): call the real mission task here
        await sleep(5)

        if self.log:
            self.log.info("Finished mission", extra={"id": mission.id})

    def _on_mission_added_to_registry(
        self, sender: MissionRegistry, *, mission: Mission
    ):
        """Signal handler that is called when a new mission is added to the
        mission registry.
        """
        self._subscribe_to_mission(mission)

    def _on_mission_removed_from_registry(
        self, sender: MissionRegistry, *, mission: Mission
    ):
        """Signal handler that is called when a mission is removed from the
        mission registry.
        """
        self._unsubscribe_from_mission(mission)
        self.cancel_mission(mission)

    def _on_mission_start_time_changed(self, sender: Mission) -> None:
        """Signal handler that is called when the start time of one of the
        missions in the registry changes.
        """
        start_time = sender.starts_at
        self._schedule_mission(sender)

        if self.log:
            if start_time is not None:
                fmt_start_time = format_timestamp_nicely(start_time)
                self.log.info(
                    f"Mission start time set to {fmt_start_time}",
                    extra={"id": sender.id},
                )
            else:
                self.log.info("Mission start time cleared", extra={"id": sender.id})

    def _schedule_mission(self, mission: Mission) -> None:
        """Updates the scheduler so the given mission is scheduled at its current
        start time.
        """
        job = self._missions_to_jobs.get(mission)
        if self.scheduler is None:
            if job is not None:
                # We have no scheduler, so we should not have a job
                # corresponding to the mission either. Nevertheless, we
                # simply remove it from the mapping
                del self._missions_to_jobs[mission]
            return

        start_time = mission.starts_at
        if start_time is not None:
            # Mission has a new start time
            if job is not None:
                # Mission already has a job so change the start time of the job
                self.scheduler.reschedule_to(start_time, job)
            else:
                # Mission does not have a job yet, so create one
                job = self.scheduler.schedule_at(start_time, self._run_mission, mission)
                self._missions_to_jobs[mission] = job
        else:
            # Mission has no start time any more
            self.cancel_mission(mission)

    @contextmanager
    def _subscribed_to_missions(self) -> Iterator[None]:
        """Context manager that subscribes to the events for all missions in the
        current registry when the context is entered and unsubscribes from them
        when the context is exited.
        """
        for mission in self._mission_registry:
            self._subscribe_to_mission(mission)
        self._mission_registry.mission_added.connect(
            self._on_mission_added_to_registry, sender=cast(Any, self._mission_registry)
        )
        self._mission_registry.mission_removed.connect(
            self._on_mission_removed_from_registry,
            sender=cast(Any, self._mission_registry),
        )
        try:
            yield
        finally:
            self._mission_registry.mission_removed.connect(
                self._on_mission_removed_from_registry,
                sender=cast(Any, self._mission_registry),
            )
            self._mission_registry.mission_added.connect(
                self._on_mission_added_to_registry,
                sender=cast(Any, self._mission_registry),
            )
            for mission in self._mission_registry:
                self._unsubscribe_from_mission(mission)

    def _subscribe_to_mission(self, mission: Mission):
        """Subscribes to the given mission."""
        mission.on_start_time_changed.connect(
            self._on_mission_start_time_changed, sender=cast(Any, mission)
        )

    def _unsubscribe_from_mission(self, mission: Mission):
        """Unsubscribes from the given mission."""
        mission.on_start_time_changed.disconnect(
            self._on_mission_start_time_changed, sender=cast(Any, mission)
        )
