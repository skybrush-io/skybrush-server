from blinker import Signal
from contextlib import ExitStack
from trio import CancelScope, open_nursery, sleep_forever
from typing import Any, Dict

from flockwave.ext.base import ExtensionBase
from flockwave.server.tasks import wait_until

from .clock import ShowClock
from .config import DroneShowConfiguration, StartMethod

__all__ = ("construct", "dependencies")


class DroneShowExtension(ExtensionBase):
    """Extension that prepares the server to be able to manage drone shows."""

    def __init__(self):
        super().__init__()

        self._clock = None
        self._clock_watcher = None
        self._nursery = None

        self._config = DroneShowConfiguration()
        self._start_signal = Signal(
            doc="""Signal that will be dispatched by the extension when the
            show is about to start.

            Dispatched only when the show is set to automatic start mode.
            """
        )

    def exports(self) -> Dict[str, Any]:
        return {"started": self._start_signal}

    def handle_SHOW_CFG(self, message, sender, hub):
        return hub.create_response_or_notification(
            body={"configuration": self._config.json}, in_response_to=message
        )

    def handle_SHOW_SETCFG(self, message, sender, hub):
        try:
            self._config.update_from_json(message.body.get("configuration", {}))
            return hub.acknowledge(message)
        except Exception as ex:
            return hub.acknowledge(message, outcome=False, reason=str(ex))

    async def run(self, app, configuration, logger):
        self._clock = ShowClock()
        handlers = {
            "SHOW-CFG": self.handle_SHOW_CFG,
            "SHOW-SETCFG": self.handle_SHOW_SETCFG,
        }

        async with open_nursery() as self._nursery:
            with ExitStack() as stack:
                stack.enter_context(
                    self._config.updated.connected_to(
                        self._on_config_updated, sender=self._config
                    )
                )
                stack.enter_context(app.import_api("clocks").use_clock(self._clock))
                stack.enter_context(app.message_hub.use_message_handlers(handlers))
                await sleep_forever()

    def _on_config_updated(self, sender):
        """Handler that is called when the configuration of the extension was
        updated from any source.
        """
        self._clock.start_time = self._config.start_time

        if self._clock_watcher is not None:
            self._clock_watcher.cancel()
            self._clock_watcher = None

        should_listen_to_clock = (
            self._config.authorized_to_start
            and self._clock.start_time is not None
            and self._config.start_method is StartMethod.AUTO
        )

        if should_listen_to_clock:
            # TODO(ntamas): what if we don't have a nursery here?
            self._clock_watcher = CancelScope()
            self._nursery.start_soon(self._start_show_when_needed, self._clock_watcher)

    async def _start_show_when_needed(self, cancel_scope):
        try:
            with self._clock_watcher:
                await wait_until(self._clock, seconds=0, edge_triggered=True)

                self._start_uavs_if_needed()
                self._start_signal.send(self)

                delay = int(self._clock.seconds * 1000)
                if delay >= 1:
                    self.log.warn(f"Started show with a delay of {delay} ms")
                else:
                    self.log.info(f"Started show accurately")
        finally:
            self._clock_watcher = None

    def _start_uavs_if_needed(self):
        uavs_by_drivers = self.app.sort_uavs_by_drivers(self._config.uav_ids)
        for driver, uavs in uavs_by_drivers.items():
            driver.send_takeoff_signal(uavs)


construct = DroneShowExtension
dependencies = ("clocks",)
