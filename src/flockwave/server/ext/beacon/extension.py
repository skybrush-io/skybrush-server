from contextlib import ExitStack
from operator import attrgetter
from trio import sleep_forever
from flockwave.server.message_hub import create_generic_INF_message_handler
from flockwave.server.model.object import registered

from ..base import ExtensionBase

from .model import Beacon, is_beacon

############################################################################


class BeaconExtension(ExtensionBase):
    """Extension that implements support for beacons."""

    async def run(self, app, configuration, logger):
        with ExitStack() as stack:
            # Register message handlers for beacon-related messages
            handle_BCN_INF = create_generic_INF_message_handler(
                app.object_registry,
                filter=is_beacon,
                getter=attrgetter("status"),
                description="beacon",
            )
            stack.enter_context(
                app.message_hub.use_message_handlers({"BCN-INF": handle_BCN_INF})
            )
            stack.enter_context(registered("beacon", Beacon))
            await sleep_forever()


construct = BeaconExtension
description = "Beacon objects"
