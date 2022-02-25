"""Extension that provides some basic facilities for mission planning and
uploading missions to UAVs.
"""

from __future__ import annotations

from contextlib import ExitStack
from inspect import isawaitable
from trio import sleep_forever
from typing import Any, Dict, Tuple, cast

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import Client, FlockwaveMessage
from flockwave.server.utils.formatting import format_list_nicely

from .examples import LandImmediatelyMissionType
from .registry import MissionRegistry, MissionTypeRegistry
from .types import MissionPlan, MissionType


class MissionManagementExtension(Extension):
    """Extension that provides some basic facilities for mission planning and
    uploading missions to UAVs.
    """

    _mission_registry: MissionRegistry
    """Registry that maps short identifiers to mission _instances_, i.e.
    actual missions that are being executed or will be executed in the near
    future.
    """

    _mission_type_registry: MissionTypeRegistry
    """Registry that maps short identifiers to mission _types_, i.e. high-level
    objects that know how to plan a mission of a given type and how to execute
    the mission.
    """

    def __init__(self):
        super().__init__()
        self._mission_registry = MissionRegistry()
        self._mission_type_registry = MissionTypeRegistry()

    def exports(self) -> Dict[str, Any]:
        return {
            "use": self._mission_type_registry.use,
        }

    async def run(self):
        assert self.app is not None

        with ExitStack() as stack:
            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        "X-MSN-NEW": self._handle_MSN_NEW,
                        "X-MSN-PLAN": self._handle_MSN_PLAN,
                    }
                )
            )
            stack.enter_context(
                self._mission_type_registry.use("land", LandImmediatelyMissionType())
            )
            await sleep_forever()

    async def _handle_MSN_NEW(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to create a new mission belonging to a
        given mission type.
        """
        try:
            mission_type, mission_type_id = self._get_mission_type_and_id_from_request(
                message
            )
            parameters = self._get_parameters_from_request(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        if mission_type is None:
            return hub.reject(message, reason="No such mission type")

        mission = self._mission_registry.create(mission_type_id)
        mission.parameters = parameters

        if self.log:
            extra = {"id": mission.id}
            self.log.info(f"Mission created, type = {mission_type_id!r}", extra=extra)
            if parameters:
                keys = format_list_nicely(
                    sorted(parameters.keys()), item_formatter=repr
                )
                self.log.info(f"Mission parameters updated: {keys}", extra=extra)

        return {"result": mission.id}

    async def _handle_MSN_PLAN(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to create a plan of a mission belonging
        to a given mission type.

        This does not create a new mission yet, only a plan. The mission itself
        can be created with an MSN-NEW message.
        """
        try:
            mission_type, _ = self._get_mission_type_and_id_from_request(message)
            parameters = self._get_parameters_from_request(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        maybe_plan = mission_type.create_plan(parameters)
        if isawaitable(maybe_plan):
            plan = cast(MissionPlan, await maybe_plan)
        else:
            plan = cast(MissionPlan, maybe_plan)

        return {"result": plan}

    def _get_mission_type_and_id_from_request(
        self, message: FlockwaveMessage
    ) -> Tuple[MissionType, str]:
        id = message.body.get("id")
        try:
            mission_type = (
                self._mission_type_registry.find_by_id(id)
                if isinstance(id, str)
                else None
            )
        except KeyError:
            mission_type = None
        if id is None or mission_type is None:
            raise RuntimeError("No such mission type")
        return mission_type, id

    def _get_parameters_from_request(self, message: FlockwaveMessage) -> Dict[str, Any]:
        parameters = message.body.get("parameters", {})
        if not isinstance(parameters, dict):
            raise RuntimeError("Parameters must be a dictionary")
        return parameters


construct = MissionManagementExtension
description = "Basic facilities for planning and uploading missions."
schema = {}
tags = "experimental"
