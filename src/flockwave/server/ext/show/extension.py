from __future__ import annotations

from contextlib import ExitStack
from logging import Logger
from math import inf
from trio import fail_after, Nursery, open_nursery, sleep_forever, TooSlowError
from trio_util import periodic
from typing import Any, Optional

from flockwave.concurrency import CancellableTaskGroup
from flockwave.server.ext.base import Extension
from flockwave.server.model.clock import Clock
from flockwave.server.tasks import wait_for_dict_items, wait_until

from .clock import ClockSynchronizationHandler, ShowClock, ShowEndClock
from .config import DroneShowConfiguration, LightConfiguration, StartMethod
from .logging import ShowUploadLoggingMiddleware

__all__ = ("construct", "dependencies", "description")


class DroneShowExtension(Extension):
    """Extension that prepares the server to be able to manage drone shows.

    The extension provides three signals via the `signals` extension; `show:start`
    is emitted when the show starts, `show:config_updated` is emitted when
    the configuration of the show startup changes, and `show:lights_updated` is
    emitted when the configuration of the LED lights on the drones changes. The
    latter two receive a keyword argument named `config` that contains the new
    configuration.
    """

    log: Logger

    _clock: Optional[ShowClock]
    _clock_sync: ClockSynchronizationHandler
    _end_clock: Optional[ShowEndClock]
    _end_clock_sync: ClockSynchronizationHandler

    _nursery: Optional[Nursery]
    _show_tasks: Optional[CancellableTaskGroup]

    def __init__(self):
        super().__init__()

        self._clock = None
        self._clock_sync = ClockSynchronizationHandler()

        self._end_clock = None
        self._end_clock_sync = ClockSynchronizationHandler()

        self._nursery = None
        self._show_tasks = None

        self._config = DroneShowConfiguration()
        self._lights = LightConfiguration()

    def exports(self) -> dict[str, Any]:
        return {
            "get_clock": self._get_clock,
            "get_configuration": self._get_configuration,
            "get_light_configuration": self._get_light_configuration,
        }

    def handle_SHOW_CFG(self, message, sender, hub):
        return hub.create_response_or_notification(
            body={"configuration": self._config.json}, in_response_to=message
        )

    def handle_SHOW_LIGHTS(self, message, sender, hub):
        return hub.create_response_or_notification(
            body={"configuration": self._lights.json}, in_response_to=message
        )

    def handle_SHOW_SETCFG(self, message, sender, hub):
        try:
            config = message.body.get("configuration", {})

            # If the "config" object contains a key named "start", and it has
            # no sub-key named "clock", it means that we are working with an
            # older version of Skybrush Live that did not have support for
            # MIDI timecode. In this case, we assume that the clock is explicitly
            # set to None
            if isinstance(config, dict) and "start" in config:
                if isinstance(config["start"], dict) and "clock" not in config["start"]:
                    config["start"]["clock"] = None

            self._config.update_from_json(config)
            return hub.acknowledge(message)
        except Exception as ex:
            return hub.acknowledge(message, outcome=False, reason=str(ex))

    def handle_SHOW_SETLIGHTS(self, message, sender, hub):
        try:
            self._lights.update_from_json(message.body.get("configuration", {}))
            return hub.acknowledge(message)
        except Exception as ex:
            return hub.acknowledge(message, outcome=False, reason=str(ex))

    async def run(self, app, configuration, logger):
        self._clock = ShowClock()
        self._end_clock = ShowEndClock()

        handlers = {
            "SHOW-CFG": self.handle_SHOW_CFG,
            "SHOW-LIGHTS": self.handle_SHOW_LIGHTS,
            "SHOW-SETCFG": self.handle_SHOW_SETCFG,
            "SHOW-SETLIGHTS": self.handle_SHOW_SETLIGHTS,
        }

        self._config.start_method = StartMethod(
            configuration.get("default_start_method", "rc")
        )

        async with open_nursery() as self._nursery:
            assert self._nursery is not None

            self._show_tasks = CancellableTaskGroup(self._nursery)

            with ExitStack() as stack:
                stack.enter_context(
                    self._config.updated.connected_to(
                        self._on_config_updated, sender=self._config  # type: ignore
                    )
                )
                stack.enter_context(
                    self._lights.updated.connected_to(
                        self._on_lights_updated, sender=self._lights  # type: ignore
                    )
                )
                stack.enter_context(
                    self._clock.started.connected_to(
                        self._on_show_clock_changed, sender=self._clock  # type: ignore
                    )
                )
                stack.enter_context(
                    self._clock.stopped.connected_to(
                        self._on_show_clock_changed, sender=self._clock  # type: ignore
                    )
                )
                stack.enter_context(
                    self._clock.changed.connected_to(
                        self._on_show_clock_changed, sender=self._clock  # type: ignore
                    )
                )
                stack.enter_context(app.import_api("clocks").use_clock(self._clock))
                stack.enter_context(app.import_api("clocks").use_clock(self._end_clock))
                stack.enter_context(app.message_hub.use_message_handlers(handlers))
                stack.enter_context(
                    app.message_hub.use_request_middleware(
                        ShowUploadLoggingMiddleware(self.log)
                    )
                )
                stack.enter_context(self._clock_sync.use_secondary_clock(self._clock))
                stack.enter_context(
                    self._end_clock_sync.use_secondary_clock(self._end_clock)
                )
                await sleep_forever()

    def _get_clock(self) -> Optional[ShowClock]:
        """Returns a reference to the show clock."""
        return self._clock

    def _get_configuration(self) -> DroneShowConfiguration:
        """Returns a copy of the current drone show configuration."""
        return self._config.clone()

    def _get_light_configuration(self) -> LightConfiguration:
        """Returns a copy of the current LED lgiht configuration."""
        return self._lights.clone()

    def _on_config_updated(self, sender) -> None:
        """Handler that is called when the configuration of the start settings
        of the show was updated from any source.
        """
        assert self.app is not None

        self._sync_show_clocks_to(
            self._config.clock, self._config.start_time_on_clock, self._config.duration
        )

        if self._show_tasks is not None:
            self._show_tasks.cancel_all()

            if self._should_run_countdown:
                self._show_tasks.start_soon(self._start_show_when_needed)
                self._show_tasks.start_soon(self._manage_countdown_before_start)

        self.log.info(self._config.format())

        assert self.app is not None
        updated_signal = self.app.import_api("signals").get("show:config_updated")
        updated_signal.send(self, config=self._config.clone())

    def _on_lights_updated(self, sender) -> None:
        """Handler that is called when the configuration of the LED lights was
        updated from any source.
        """
        assert self.app is not None
        updated_signal = self.app.import_api("signals").get("show:lights_updated")
        updated_signal.send(self, config=self._lights.clone())

    def _on_show_clock_changed(self, sender, *, delta: Optional[float] = None) -> None:
        """Handler that is called when the show clock is started, stopped or
        adjusted.
        """
        assert self.app is not None
        changed_signal = self.app.import_api("signals").get("show:clock_changed")
        changed_signal.send(self)

    @property
    def _should_run_countdown(self) -> bool:
        """Returns whether the extension should run the clock countdown, given
        its current configuration.
        """
        return (
            self._config.authorized_to_start
            and self._clock is not None
            and self._clock.start_time is not None
            and self._config.start_method is StartMethod.AUTO
        )

    def _sync_show_clocks_to(
        self, clock_id: Optional[str], time: Optional[float], duration: Optional[float]
    ) -> None:
        """Configures the clock synchronization handler such that it syncs the
        show clock to the clock with the given ID, assuming that the show clock
        must reach zero when the clock with the given ID reaches the given
        time, in seconds.

        When the clock ID is ``None``, assumes that the given time is an absolute
        start time expressed as the number of seconds since the UNIX epoch.

        Args:
            clock_id: ID of the clock to synchronize the show clock to, or
                ``None`` if we must synchronize to absolute time
            time: timestamp to synchronize the start to
            duration: expected duration of the show, in seconds
        """
        primary_clock: Optional[Clock]

        end_time = time if time is not None else None
        if end_time is not None and duration is not None:
            end_time += duration

        if self.app is None:
            self._clock_sync.disable_and_stop()
            self._end_clock_sync.disable_and_stop()

        elif clock_id is None:
            self._clock_sync.disable()
            self._end_clock_sync.disable()
            if self._clock is not None:
                self._clock.reference_time = time
            if self._end_clock is not None:
                self._end_clock.reference_time = end_time

        else:
            registry = self.app.import_api("clocks").registry
            try:
                primary_clock = registry.find_by_id(clock_id)
            except KeyError:
                primary_clock = None

            if primary_clock and time is not None:
                self._clock_sync.synchronize_to(primary_clock, time)
                if end_time is not None:
                    self._end_clock_sync.synchronize_to(primary_clock, end_time)
                else:
                    self._end_clock_sync.disable_and_stop()
            else:
                self._clock_sync.disable_and_stop()
                self._end_clock_sync.disable_and_stop()

    async def _start_show_when_needed(self) -> None:
        assert self.app is not None
        start_signal = self.app.import_api("signals").get("show:start")

        assert self._clock is not None
        await wait_until(self._clock, seconds=0, edge_triggered=True)

        self._start_uavs_if_needed()
        start_signal.send(self)

        delay = int(self._clock.seconds * 1000)
        if delay >= 1:
            self.log.warning(f"Started show with a delay of {delay} ms")
        else:
            self.log.info("Started show accurately")

    async def _manage_countdown_before_start(self) -> None:
        assert self._clock is not None
        await wait_until(self._clock, seconds=-11, edge_triggered=False)

        last_seconds = -inf
        try:
            async for _ in periodic(1):
                seconds = self._clock.seconds
                if not self._should_run_countdown:
                    break
                elif last_seconds > seconds:
                    self._notify_uavs_about_countdown_state(cancelled=True)
                elif seconds > -0.5:
                    break
                else:
                    self._notify_uavs_about_countdown_state(seconds_left=-seconds)
                    last_seconds = seconds
        finally:
            # Cancel any countdowns that we may have started if the clock was
            # stopped or the authorization was revoked
            if not self._should_run_countdown:
                self._notify_uavs_about_countdown_state(cancelled=True)

    def _notify_uavs_about_countdown_state(
        self, seconds_left: float = 0, cancelled: bool = False
    ) -> None:
        assert self.app is not None
        countdown_signal = self.app.import_api("signals").get("show:countdown")
        countdown_signal.send(self, delay=seconds_left if not cancelled else None)

    def _start_uavs_if_needed(self) -> None:
        assert self.app is not None
        assert self._nursery is not None

        self._notify_uavs_about_countdown_state(seconds_left=0)

        uav_ids = (uav_id for uav_id in self._config.uav_ids if uav_id is not None)
        uavs_by_drivers = self.app.sort_uavs_by_drivers(uav_ids)
        for driver, uavs in uavs_by_drivers.items():
            results = driver.send_takeoff_signal(uavs, scheduled=True)
            self._nursery.start_soon(
                self._process_command_results_in_background, results, "start signals"
            )

    async def _process_command_results_in_background(
        self, results, what: str = "commands"
    ) -> None:
        try:
            with fail_after(5):
                results = await wait_for_dict_items(results)
        except TooSlowError:
            self.log.warning(
                f"Failed to send {what} to {len(results)} UAVs in 5 seconds"
            )
            return

        failed = [key for key, value in results.items() if isinstance(value, Exception)]
        if failed:
            failed = ", ".join([getattr(uav, "id", "-no-id-") for uav in failed])
            self.log.warning(f"Failed to send {what} to {failed}")


construct = DroneShowExtension
dependencies = ("clocks", "signals")
description = "Support for managing drone shows"
schema = {
    "properties": {
        "default_start_method": {
            "type": "string",
            "title": "Default start method for shows",
            "enum": [StartMethod.RC.value, StartMethod.AUTO.value],
            "default": StartMethod.RC.value,
            "options": {
                "enum_titles": [StartMethod.RC.describe(), StartMethod.AUTO.describe()],
            },
        }
    }
}
