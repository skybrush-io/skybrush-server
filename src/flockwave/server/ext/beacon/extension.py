from __future__ import annotations

from contextlib import ExitStack, contextmanager
from operator import attrgetter
from typing import Iterator, Optional

from flockwave.concurrency import AsyncBundler
from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import (
    create_generic_INF_or_PROPS_message_factory,
    create_multi_object_message_handler,
)
from flockwave.server.model.object import registered
from flockwave.server.registries.base import find_in_registry

from flockwave.server.model.messages import FlockwaveNotification

from .model import Beacon, is_beacon

############################################################################


class BeaconExtension(Extension):
    """Extension that implements support for beacons."""

    beacons_to_update: AsyncBundler[str]

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

    def _remove_beacon(self, beacon: Beacon) -> None:
        if not is_beacon(beacon):
            raise TypeError(f"expected beacon, got {type(beacon)!r}")

        beacon.updated.disconnect(self._on_beacon_updated, sender=beacon)

        assert self.app is not None
        self.app.object_registry.remove(beacon)

    @contextmanager
    def _use_beacon(self, beacon_id: str) -> Iterator[Beacon]:
        assert self.app is not None

        beacon = Beacon(id=beacon_id)
        with self.app.object_registry.use(beacon):
            with beacon.updated.connected_to(self._on_beacon_updated, sender=beacon):  # type: ignore
                yield beacon

    def exports(self):
        return {
            "find_by_id": self._find_beacon_by_id,
            "use": self._use_beacon,
        }

    async def run(self):
        app = self.app
        assert app is not None

        with ExitStack() as stack:
            self.beacons_to_update = AsyncBundler()

            # Register message handlers for beacon-related messages
            create_BCN_INF = create_generic_INF_or_PROPS_message_factory(
                "BCN-INF",
                "status",
                app.object_registry,
                filter=is_beacon,
                getter=attrgetter("status"),
                description="beacon",
            )
            create_BCN_PROPS = create_generic_INF_or_PROPS_message_factory(
                "BCN-PROPS",
                "result",
                app.object_registry,
                filter=is_beacon,
                getter=attrgetter("basic_properties"),
                description="beacon",
            )

            stack.enter_context(
                app.message_hub.use_message_handlers(
                    {
                        "BCN-INF": create_multi_object_message_handler(create_BCN_INF),
                        "BCN-PROPS": create_multi_object_message_handler(
                            create_BCN_PROPS
                        ),
                    }
                )
            )
            stack.enter_context(registered("beacon", Beacon))

            async for bundle in self.beacons_to_update:
                message = create_BCN_INF(app.message_hub, bundle, None)
                if isinstance(message, FlockwaveNotification):
                    await app.message_hub.broadcast_message(message)


construct = BeaconExtension
description = "Beacon objects"
schema = {}
