from trio import sleep_forever

from flockwave.ext.base import ExtensionBase

from .clock import ShowClock
from .config import DroneShowConfiguration

__all__ = ("construct", "dependencies")


class DroneShowExtension(ExtensionBase):
    """Extension that prepares the server to be able to manage drone shows."""

    def __init__(self):
        super().__init__()
        self._config = DroneShowConfiguration()

    def handle_SHOW_CFG(self, message, sender, hub):
        return hub.create_response_or_notification(
            body={"configuration": self._config.json}, in_response_to=message
        )

    async def run(self, app, configuration, logger):
        clock = ShowClock()
        handlers = {"SHOW-CFG": self.handle_SHOW_CFG}

        with app.message_hub.use_message_handlers(handlers):
            with app.import_api("clocks").use_clock(clock):
                await sleep_forever()


construct = DroneShowExtension
dependencies = ("clocks",)
