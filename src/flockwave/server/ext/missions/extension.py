"""Extension that provides some basic facilities for mission planning and
uploading missions to UAVs.
"""

from __future__ import annotations

from contextlib import ExitStack
from inspect import isawaitable
from trio import sleep_forever
from typing import Any, Dict, Iterable, Optional, Tuple, cast

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import Client, FlockwaveMessage, FlockwaveResponse
from flockwave.server.registries import find_in_registry
from flockwave.server.utils.formatting import format_list_nicely

from .examples import LandImmediatelyMissionType
from .model import Mission, MissionPlan, MissionType
from .registry import MissionRegistry, MissionTypeRegistry


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
        self._mission_type_registry = MissionTypeRegistry()
        self._mission_registry = MissionRegistry(self._mission_type_registry)

    def exports(self) -> Dict[str, Any]:
        return {
            "use": self._mission_type_registry.use,
        }

    def find_mission_by_id(
        self, mission_id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[Mission]:
        """Finds the UAV with the given ID in the object registry or registers
        a failure in the given response object if there is no UAV with the
        given ID.

        Parameters:
            uav_id: the ID of the UAV to find
            response: the response in which the failure can be registered

        Returns:
            the UAV with the given ID or ``None`` if there is no such UAV
        """
        return find_in_registry(
            self._mission_registry,
            mission_id,
            response=response,
            failure_reason="No such mission",
        )

    async def run(self):
        assert self.app is not None

        with ExitStack() as stack:
            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        "X-MSN-INF": self._handle_MSN_INF,
                        "X-MSN-NEW": self._handle_MSN_NEW,
                        "X-MSN-PLAN": self._handle_MSN_PLAN,
                    }
                )
            )
            stack.enter_context(
                self._mission_type_registry.use("land", LandImmediatelyMissionType())
            )
            await sleep_forever()

    def _create_MSN_INF_message_for(
        self,
        mission_ids: Iterable[str],
        in_response_to: Optional[FlockwaveMessage] = None,
    ):
        """Creates an MSN-INF message that contains information regarding
        the missions with the given IDs.

        Parameters:
            mission_ids: list of mission IDs
            in_response_to: the message that the constructed message will
                respond to. ``None`` means that the constructed message will be
                a notification.

        Returns:
            FlockwaveMessage: the MSN-INF message with the status info of
                the given UAVs
        """
        if self.app is None:
            raise RuntimeError("application is not set")

        statuses = {}

        body = {"status": statuses, "type": "MSN-INF"}
        response = self.app.message_hub.create_response_or_notification(
            body=body, in_response_to=in_response_to
        )

        for mission_id in mission_ids:
            mission = self.find_mission_by_id(mission_id, response)  # type: ignore
            if mission:
                statuses[mission_id] = mission.json

        return response

    async def _handle_MSN_INF(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to return the current state of one or
        more missions.
        """
        return self._create_MSN_INF_message_for(
            message.get_ids(), in_response_to=message
        )

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

        try:
            mission = self._mission_registry.create(mission_type_id)
        except Exception as ex:
            return hub.reject(message, reason=f"Error while creating mission: {ex}")

        try:
            mission.update_parameters(parameters)
        except Exception as ex:
            return hub.reject(
                message, reason=f"Error while setting parameters for mission: {ex}"
            )

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
