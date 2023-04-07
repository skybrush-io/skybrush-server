"""Extension that provides some basic facilities for local positioning systems."""

from __future__ import annotations

from contextlib import ExitStack
from operator import attrgetter, methodcaller
from trio import sleep_forever
from typing import Any, Dict, Optional

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.message_handlers import (
    create_generic_INF_or_PROPS_message_factory,
    create_multi_object_message_handler,
)
from flockwave.server.model import Client, FlockwaveMessage, FlockwaveResponse
from flockwave.server.registries import find_in_registry

from .examples import DummyLocalPositioningSystemType
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

    def __init__(self):
        super().__init__()
        self._lps_type_registry = LocalPositioningSystemTypeRegistry()
        self._lps_registry = LocalPositioningSystemRegistry(self._lps_type_registry)

    def exports(self) -> Dict[str, Any]:
        return {
            "find_lps_by_id": self.find_lps_by_id,
            "use_lps_type": self._lps_type_registry.use,
        }

    def find_lps_by_id(
        self, id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[LocalPositioningSystem]:
        """Finds the local positioning system (LPS) with the given ID in the
        LPS type registry or registers a failure in the given response object if
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
        handle_LPS_INF = handle_many_with(
            create_generic_INF_or_PROPS_message_factory(
                "X-LPS-INF",
                "status",
                self._lps_registry,
                getter=attrgetter("json"),
                description="local positioning system",
            )
        )
        handle_LPS_TYPE_INF = handle_many_with(
            create_generic_INF_or_PROPS_message_factory(
                "X-LPS-TYPE-INF",
                "items",
                self._lps_type_registry,
                getter=methodcaller("describe"),
                description="local positioning system type",
                add_object_id=True,
            )
        )
        handle_LPS_TYPE_SCHEMA = handle_many_with(
            create_generic_INF_or_PROPS_message_factory(
                "X-LPS-TYPE-SCHEMA",
                "items",
                self._lps_type_registry,
                getter=methodcaller("get_configuration_schema"),
                description="local positioning system type",
            )
        )

        with ExitStack() as stack:
            stack.enter_context(
                self._lps_registry.use_object_registry(self.app.object_registry)
            )

            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        "X-LPS-CALIB": self._handle_LPS_CALIB,
                        # "X-LPS-CFG": self._handle_LPS_CFG,
                        "X-LPS-INF": handle_LPS_INF,
                        "X-LPS-LIST": self._handle_LPS_LIST,
                        "X-LPS-TYPE-INF": handle_LPS_TYPE_INF,
                        "X-LPS-TYPE-LIST": self._handle_LPS_TYPE_LIST,
                        "X-LPS-TYPE-SCHEMA": handle_LPS_TYPE_SCHEMA,
                    }
                )
            )

            stack.enter_context(
                self._lps_type_registry.use("dummy", DummyLocalPositioningSystemType())
            )

            stack.enter_context(self._lps_registry.create_and_use("dummy"))

            lps_ids = list(self._lps_registry.ids)
            if self.log:
                self.log.info(f"LPS instance registered with ID={lps_ids}")

            await sleep_forever()

    async def _handle_LPS_CALIB(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to perform a calibration of a local
        positioning system instance.
        """
        assert self.app is not None
        return await self.app.dispatch_to_objects(
            message, sender, method_name="calibrate"
        )

    def _handle_LPS_LIST(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the IDs of all registered
        local positioning system (LPS) instances.
        """
        return {"ids": list(self._lps_registry.ids)}

    def _handle_LPS_TYPE_LIST(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the IDs of all registered
        local positioning system (LPS) types.
        """
        return {"ids": list(self._lps_type_registry.ids)}

    def _handle_LPS_TYPE_SCHEMA(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the JSON schems associated
        with the parameters of one or more local positioning system (LPS) types.
        """
        items = {}

        body = {"items": items, "type": "X-LPS-TYPE-SCHEMA"}
        response = hub.create_response_or_notification(
            body=body, in_response_to=message
        )

        for id in message.body.get("ids", ()):
            lps_type = self.find_lps_type_by_id(id, response)
            if lps_type:
                items[id] = lps_type.get_configuration_schema()

        return response


construct = LocalPositioningSystemsExtension
description = "Basic facilities for local positioning systems"
schema = {}
tags = "experimental"
