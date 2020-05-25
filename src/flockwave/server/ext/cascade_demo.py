"""Experimental extension to demonstrate the connection between an ERP system
and a Skybrush server, in collaboration with Cascade Ltd
"""

from trio import sleep_forever

from .base import ExtensionBase


class ERPSystemConnectionDemoExtension(ExtensionBase):
    """Experimental extension to demonstrate the connection between an ERP system
    and a Skybrush server, in collaboration with Cascade Ltd
    """

    def handle_trip_addition(self, message, sender, hub):
        """Handles the addition of a new trip to the list of scheduled trips."""
        return hub.acknowledge(message)

    def handle_trip_cancellation(self, message, sender, hub):
        """Cancels the current trip on a given drone."""
        return hub.acknowledge(message)

    async def run(self):
        handlers = {
            "X-TRIP-ADD": self.handle_trip_addition,
            "X-TRIP-CANCEL": self.handle_trip_cancellation,
        }
        with self.app.message_hub.use_message_handlers(handlers):
            await sleep_forever()


construct = ERPSystemConnectionDemoExtension
