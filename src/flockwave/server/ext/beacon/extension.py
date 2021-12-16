from __future__ import annotations

from contextlib import ExitStack
from operator import attrgetter
from typing import ContextManager, Optional, TYPE_CHECKING

from flockwave.concurrency import AsyncBundler
from flockwave.server.ext.base import ExtensionBase
from flockwave.server.message_hub import (
    create_generic_INF_message_factory,
    create_generic_INF_message_handler,
)
from flockwave.server.model.object import registered
from flockwave.server.registries.base import find_in_registry

from flockwave.server.model.messages import FlockwaveNotification

from .model import Beacon, is_beacon

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

############################################################################


class BeaconExtension(ExtensionBase):
    """Extension that implements support for beacons."""

    app: "SkybrushServer"
    beacons_to_update: AsyncBundler[str]

    def _add_beacon(self, beacon_id: str) -> Beacon:
        beacon = Beacon(id=beacon_id)
        self.app.object_registry.add(beacon)
        beacon.updated.connect(self._on_beacon_updated, sender=beacon, weak=True)
        return beacon

    def _find_beacon_by_id(self, id: str) -> Optional[Beacon]:
        """Finds a beacon by its ID in the object registry.

        Parameters:
            id: the ID of the beacon to find

        Returns:
            the beacon or `None` if there is no such beacon
        """
        return find_in_registry(self.app.object_registry, id, predicate=is_beacon)  # type: ignore

    def _on_beacon_updated(self, sender: Beacon) -> None:
        """Blinker signal handler that marks the beacon as changed so we dispatch
        a BCN-INF message for it next time we get around to doing so.
        """
        if self.beacons_to_update:
            self.beacons_to_update.add(sender.id)

    def _use_beacon(self, beacon: Beacon) -> ContextManager[None]:
        if not is_beacon(beacon):
            raise TypeError("expected beacon, got {beacon!r}")

        return self.app.object_registry.use(beacon)

    def exports(self):
        return {
            "add": self._add_beacon,
            "find_by_id": self._find_beacon_by_id,
            "use": self._use_beacon,
        }

    async def run(self, app: "SkybrushServer", configuration, logger):
        with ExitStack() as stack:
            self.beacons_to_update = AsyncBundler()

            # Register message handlers for beacon-related messages
            create_BCN_INF = create_generic_INF_message_factory(
                "BCN-INF",
                app.object_registry,
                filter=is_beacon,
                getter=attrgetter("status"),
                description="beacon",
            )
            handle_BCN_INF = create_generic_INF_message_handler(create_BCN_INF)
            stack.enter_context(
                app.message_hub.use_message_handlers({"BCN-INF": handle_BCN_INF})
            )
            stack.enter_context(registered("beacon", Beacon))

            async for bundle in self.beacons_to_update:
                message = create_BCN_INF(app.message_hub, bundle, None)
                if isinstance(message, FlockwaveNotification):
                    await app.message_hub.broadcast_message(message)


construct = BeaconExtension
description = "Beacon objects"
schema = {}
