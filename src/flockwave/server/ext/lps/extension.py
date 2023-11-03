"""Extension that provides some basic facilities for local positioning systems."""

from __future__ import annotations

from contextlib import ExitStack
from operator import attrgetter, methodcaller
from typing import Any, Optional

from flockwave.concurrency import AsyncBundler
from flockwave.server.ext.base import Extension
from flockwave.server.message_handlers import (
    create_mapper,
    create_object_listing_request_handler,
    create_multi_object_message_handler,
)
from flockwave.server.model import FlockwaveResponse
from flockwave.server.model.messages import FlockwaveNotification
from flockwave.server.model.object import registered
from flockwave.server.registries import find_in_registry

from .model import LocalPositioningSystem, LocalPositioningSystemType
from .registry import LocalPositioningSystemRegistry, LocalPositioningSystemTypeRegistry


class LocalPositioningSystemsExtension(Extension):
    """Extension that provides some basic facilities for local positioning
    systems (LPS).
    """

    _lps_registry: LocalPositioningSystemRegistry
    """Registry that maps short identifiers to local positioning system (LPS)
    _instances_.
    """

    _lps_type_registry: LocalPositioningSystemTypeRegistry
    """Registry that maps short identifiers to local positioning system _types_,
    i.e. high-level descriptions and parameterizations of local position systems
    (LPS) for which specific instances may be created in the LPS registry.
    """

    _lps_to_update: Optional[AsyncBundler[str]]
    """Async bundler that collects the list of LPS objects for which we need to
    dispatch an LPS-INF message.
    """

    def __init__(self):
        super().__init__()

        self._lps_to_update = None
        self._lps_type_registry = LocalPositioningSystemTypeRegistry()
        self._lps_registry = LocalPositioningSystemRegistry(self._lps_type_registry)

    def exports(self) -> dict[str, Any]:
        return {
            "create_and_use_lps": self._lps_registry.create_and_use,
            "find_lps_by_id": self.find_lps_by_id,
            "find_lps_type_by_id": self.find_lps_type_by_id,
            "use_lps_type": self._lps_type_registry.use,
        }

    def find_lps_by_id(
        self, id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[LocalPositioningSystem]:
        """Finds the local positioning system (LPS) with the given ID in the
        LPS registry or registers a failure in the given response object if
        there is no LPS instance the given ID.

        Parameters:
            id: the ID of the LPS instance to find
            response: the response in which the failure can be registered

        Returns:
            the LPS instance with the given ID or ``None`` if there is no such LPS
        """
        return find_in_registry(
            self._lps_registry,
            id,
            response=response,
            failure_reason="No such local positioning system",
        )

    def find_lps_type_by_id(
        self, id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[LocalPositioningSystemType]:
        """Finds the local positioning system (LPS) type with the given ID in
        the LPS type registry or registers a failure in the given response
        object if there is no LPS type with the given ID.

        Parameters:
            id: the ID of the LPS type to find
            response: the response in which the failure can be registered

        Returns:
            the LPS type with the given ID or ``None`` if there is no such LPS
        """
        return find_in_registry(
            self._lps_type_registry,
            id,
            response=response,
            failure_reason="No such local positioning system type",
        )

    async def run(self):
        assert self.app is not None

        handle_many_with = create_multi_object_message_handler

        create_LPS_INF = create_mapper(
            "X-LPS-INF",
            self._lps_registry,
            key="status",
            getter=attrgetter("json"),
            description="local positioning system",
        )

        handle_LPS_CALIB = handle_many_with(
            create_mapper(
                "X-LPS-CALIB",
                self._lps_registry,
                getter=methodcaller("calibrate"),
                description="local positioning system",
                cmd_manager=self.app.command_execution_manager,
            )
        )
        handle_LPS_INF = handle_many_with(create_LPS_INF)
        handle_LPS_LIST = create_object_listing_request_handler(self._lps_registry)
        handle_LPS_TYPE_INF = handle_many_with(
            create_mapper(
                "X-LPS-TYPE-INF",
                self._lps_type_registry,
                key="items",
                getter=methodcaller("describe"),
                description="local positioning system type",
                add_object_id=True,
            )
        )
        handle_LPS_TYPE_LIST = create_object_listing_request_handler(
            self._lps_type_registry
        )
        handle_LPS_TYPE_SCHEMA = handle_many_with(
            create_mapper(
                "X-LPS-TYPE-SCHEMA",
                self._lps_type_registry,
                key="items",
                getter=methodcaller("get_configuration_schema"),
                description="local positioning system type",
            )
        )

        with ExitStack() as stack:
            self._lps_to_update = AsyncBundler()

            stack.enter_context(
                self._lps_registry.use_object_registry(self.app.object_registry)
            )
            stack.enter_context(
                self._lps_registry.added.connected_to(
                    self._on_lps_added, sender=self._lps_registry
                )
            )
            stack.enter_context(
                self._lps_registry.added.connected_to(
                    self._on_lps_removed, sender=self._lps_registry
                )
            )
            stack.enter_context(registered("lps", LocalPositioningSystem))

            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        "X-LPS-CALIB": handle_LPS_CALIB,
                        # "X-LPS-CFG": self._handle_LPS_CFG,
                        "X-LPS-INF": handle_LPS_INF,
                        "X-LPS-LIST": handle_LPS_LIST,
                        "X-LPS-TYPE-INF": handle_LPS_TYPE_INF,
                        "X-LPS-TYPE-LIST": handle_LPS_TYPE_LIST,
                        "X-LPS-TYPE-SCHEMA": handle_LPS_TYPE_SCHEMA,
                    }
                )
            )

            async for bundle in self._lps_to_update:
                message = create_LPS_INF(self.app.message_hub, bundle, None, None)
                if isinstance(message, FlockwaveNotification):
                    await self.app.message_hub.broadcast_message(message)

    def _on_lps_added(self, sender, object: LocalPositioningSystem):
        object.on_updated.connect(self._on_lps_updated, sender=object)
        object.notify_updated()

    def _on_lps_removed(self, sender, object: LocalPositioningSystem):
        object.on_updated.disconnect(self._on_lps_updated, sender=object)

    def _on_lps_updated(self, sender: LocalPositioningSystem):
        if self._lps_to_update:
            self._lps_to_update.add(sender.id)


construct = LocalPositioningSystemsExtension
description = "Basic facilities for local positioning systems"
schema = {}
tags = "experimental"
