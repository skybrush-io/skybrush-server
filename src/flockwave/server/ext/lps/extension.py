"""Extension that provides some basic facilities for local positioning systems."""

from __future__ import annotations

from contextlib import ExitStack
from trio import sleep_forever
from typing import Any, Dict, Iterable, Optional, Tuple, overload

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import Client, FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.messages import FlockwaveNotification
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

        with ExitStack() as stack:
            stack.enter_context(
                self._lps_registry.use_object_registry(self.app.object_registry)
            )
            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        # "X-LPS-CALIB": self._handle_LPS_CALIB,
                        # "X-LPS-CFG": self._handle_LPS_CFG,
                        "X-LPS-INF": self._handle_LPS_INF,
                        "X-LPS-LIST": self._handle_LPS_LIST,
                        "X-LPS-TYPE-INF": self._handle_LPS_TYPE_INF,
                        "X-LPS-TYPE-LIST": self._handle_LPS_TYPE_LIST,
                        "X-LPS-TYPE-SCHEMA": self._handle_LPS_TYPE_SCHEMA,
                    }
                )
            )
            await sleep_forever()

    @overload
    def _create_LPS_INF_message_for(
        self, lps_ids: Iterable[str]
    ) -> FlockwaveNotification:
        ...

    @overload
    def _create_LPS_INF_message_for(
        self,
        lps_ids: Iterable[str],
        in_response_to: FlockwaveMessage,
    ) -> FlockwaveResponse:
        ...

    def _create_LPS_INF_message_for(
        self,
        lps_ids: Iterable[str],
        in_response_to: Optional[FlockwaveMessage] = None,
    ):
        """Creates an LPS-INF message that contains information regarding
        the local positioning systems (LPS) with the given IDs.

        Parameters:
            lps_ids: list of LPS IDs
            in_response_to: the message that the constructed message will
                respond to. ``None`` means that the constructed message will be
                a notification.

        Returns:
            the LPS-INF message with the status info of the given LPS instances
        """
        if self.app is None:
            raise RuntimeError("application is not set")

        statuses = {}

        body = {"status": statuses, "type": "LPS-INF"}
        response = self.app.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for lps_id in lps_ids:
            lps = self.find_lps_by_id(lps_id, response)  # type: ignore
            if lps:
                statuses[lps_id] = lps.json

        return response

    async def _handle_LPS_INF(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the current state of one or
        more local positioning system instances (LPS).
        """
        return self._create_LPS_INF_message_for(
            message.get_ids(), in_response_to=message
        )

    def _handle_LPS_LIST(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the IDs of all registered
        local positioning system (LPS) instances.
        """
        return {"ids": list(self._lps_registry.ids)}

    def _handle_LPS_TYPE_INF(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the name and description of
        one or more local positioning system (LPS) types.

        Schemas are not included as they tend to be large; use LPS-TYPE-SCHEMA
        for that.
        """
        items = {}

        body = {"items": items, "type": "X-LPS-TYPE-INF"}
        response = hub.create_response_or_notification(
            body=body, in_response_to=message
        )

        for id in message.body.get("ids", ()):
            lps_type = self.find_lps_type_by_id(id, response)
            if not lps_type:
                continue

            items[id] = {
                "id": id,
                "name": lps_type.name,
                "description": lps_type.description,
            }

        return response

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

    def _get_lps_from_request_by_id(
        self, message: FlockwaveMessage
    ) -> LocalPositioningSystem:
        id = message.body.get("id") or ""
        lps = self.find_lps_by_id(id)
        if lps is None:
            raise RuntimeError("No such local positioning system")
        return lps

    def _get_lps_type_and_id_from_request(
        self, message: FlockwaveMessage
    ) -> Tuple[LocalPositioningSystemType, str]:
        id = message.body.get("id") or ""
        lps_type = self.find_lps_type_by_id(id)
        if lps_type is None:
            raise RuntimeError("No such local positioning system type")
        return lps_type, id


construct = LocalPositioningSystemsExtension
description = "Basic facilities for local positioning systems"
schema = {}
tags = "experimental"
