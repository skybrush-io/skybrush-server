from trio import sleep_forever

from flockwave.ext.base import ExtensionBase

from .clock import ShowClock
from .config import DroneShowConfiguration

__all__ = ("construct", "dependencies")


class DroneShowExtension(ExtensionBase):
    """Extension that prepares the server to be able to manage drone shows."""

    def __init__(self):
        super().__init__()

        self._clock = None
        self._config = DroneShowConfiguration()

    def handle_SHOW_CFG(self, message, sender, hub):
        return hub.create_response_or_notification(
            body={"configuration": self._config.json}, in_response_to=message
        )

    def handle_SHOW_SETCFG(self, message, sender, hub):
        try:
            self._config.update_from_json(message.body.get("configuration", {}))
            self._clock.start_time = self._config.start_time
            return hub.acknowledge(message)
        except Exception as ex:
            return hub.acknowledge(message, outcome=False, reason=str(ex))

    async def run(self, app, configuration, logger):
        self._clock = ShowClock()
        handlers = {
            "SHOW-CFG": self.handle_SHOW_CFG,
            "SHOW-SETCFG": self.handle_SHOW_SETCFG,
        }

        with app.import_api("clocks").use_clock(self._clock):
            with app.message_hub.use_message_handlers(handlers):
                await sleep_forever()


construct = DroneShowExtension
dependencies = ("clocks",)
