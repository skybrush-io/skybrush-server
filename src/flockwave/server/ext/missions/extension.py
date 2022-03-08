"""Extension that provides some basic facilities for mission planning and
uploading missions to UAVs.
"""

from __future__ import annotations

from contextlib import ExitStack
from functools import partial
from inspect import isawaitable
from trio import open_nursery
from typing import Any, Dict, Iterable, Optional, Tuple, cast, overload

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import Client, FlockwaveMessage, FlockwaveResponse
from flockwave.server.model.messages import FlockwaveNotification
from flockwave.server.registries import find_in_registry
from flockwave.server.utils.formatting import format_list_nicely

from .examples import LandImmediatelyMissionType
from .model import Mission, MissionPlan, MissionType
from .registry import MissionRegistry, MissionTypeRegistry
from .tasks import MissionSchedulerTask, MissionUpdateNotifierTask


class MissionManagementExtension(Extension):
    """Extension that provides some basic facilities for mission planning and
    execution.
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
            "find_mission_by_id": self.find_mission_by_id,
            "use_mission_type": self._mission_type_registry.use,
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
                self._mission_type_registry.use("land", LandImmediatelyMissionType())
            )
            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {
                        "X-MSN-AUTH": self._handle_MSN_AUTH,
                        "X-MSN-CANCEL": self._handle_MSN_CANCEL,
                        "X-MSN-INF": self._handle_MSN_INF,
                        "X-MSN-NEW": self._handle_MSN_NEW,
                        "X-MSN-PARAM": self._handle_MSN_PARAM,
                        "X-MSN-PLAN": self._handle_MSN_PLAN,
                        "X-MSN-SCHED": self._handle_MSN_SCHED,
                        "X-MSN-START": self._handle_MSN_START,
                    }
                )
            )
            stack.enter_context(
                self._mission_registry.use_object_registry(self.app.object_registry)
            )

            scheduler = MissionSchedulerTask(self._mission_registry)
            notifier = MissionUpdateNotifierTask(self._mission_registry)

            async with open_nursery() as nursery:
                nursery.start_soon(partial(scheduler.run, log=self.log))
                nursery.start_soon(
                    partial(
                        notifier.run,
                        log=self.log,
                        notify_update=self._broadcast_MSN_INF_message_for,
                    )
                )

    async def _broadcast_MSN_INF_message_for(self, mission_ids: Iterable[str]):
        hub = self.app.message_hub if self.app else None
        if hub:
            await hub.broadcast_message(self._create_MSN_INF_message_for(mission_ids))

    @overload
    def _create_MSN_INF_message_for(
        self, mission_ids: Iterable[str]
    ) -> FlockwaveNotification:
        ...

    @overload
    def _create_MSN_INF_message_for(
        self,
        mission_ids: Iterable[str],
        in_response_to: FlockwaveMessage,
    ) -> FlockwaveResponse:
        ...

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
            the MSN-INF message with the status info of the given missions
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

    async def _handle_MSN_AUTH(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to change the authorization state of a
        mission.
        """
        try:
            mission = self._get_mission_from_request_by_id(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        if "authorized" not in message.body:
            return hub.reject(message, reason="Missing authorization state")

        authorized = message.body["authorized"]
        if not isinstance(authorized, bool):
            return hub.reject(message, reason="Authorization state must be a boolean")

        try:
            if authorized:
                mission.authorize_to_start()
            else:
                mission.revoke_authorization()
        except Exception as ex:
            return hub.reject(
                message,
                reason=f"Error while updating authorization state of mission: {ex}",
            )

        return hub.acknowledge(message)

    async def _handle_MSN_CANCEL(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to cancel a running mission."""
        try:
            mission = self._get_mission_from_request_by_id(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        if not mission.cancel_requested:
            try:
                mission.cancel()
            except Exception as ex:
                return hub.reject(
                    message, reason=f"Error while cancelling mission: {ex}"
                )

            if self.log and not mission.is_started:
                # If the mission is already started, it will get a Cancelled
                # exception, which is logged by the mission itself
                self.log.info("Mission cancelled", extra={"id": mission.id})

        return hub.acknowledge(message)

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
            self._mission_registry.remove_by_id(mission.id)
            return hub.reject(
                message, reason=f"Error while setting parameters for mission: {ex}"
            )

        if self.log:
            self.log.info(
                f"Mission created, type = {mission_type_id!r}", extra={"id": mission.id}
            )
            if parameters:
                keys = format_list_nicely(
                    sorted(parameters.keys()), item_formatter=repr
                )
                self.log.info(
                    f"Mission parameters updated: {keys}", extra={"id": mission.id}
                )

        return {"result": mission.id}

    async def _handle_MSN_PARAM(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to update one or more parameters of a
        mission.
        """
        try:
            mission = self._get_mission_from_request_by_id(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        parameters = message.body.get("parameters")
        if not parameters or not isinstance(parameters, dict):
            return hub.reject(message, reason="Parameters must be a dictionary")

        try:
            mission.update_parameters(parameters)
        except Exception as ex:
            return hub.reject(
                message, reason=f"Error while setting parameters for mission: {ex}"
            )

        if parameters:
            if self.log:
                keys = format_list_nicely(
                    sorted(parameters.keys()), item_formatter=repr
                )
                self.log.info(
                    f"Mission parameters updated: {keys}", extra={"id": mission.id}
                )

        return hub.acknowledge(message)

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

    async def _handle_MSN_SCHED(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to set or clear the scheduled start time
        of a mission.
        """
        try:
            mission = self._get_mission_from_request_by_id(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        if "startTime" not in message.body:
            return hub.reject(message, reason="Missing start time")

        start_time = message.body["startTime"]
        if start_time is not None and not isinstance(start_time, (int, float)):
            return hub.reject(
                message, reason="Start time must be a UNIX timestamp or null"
            )

        start_time = None if start_time is None else float(start_time)

        try:
            mission.update_start_time(start_time)
        except Exception as ex:
            return hub.reject(
                message, reason=f"Error while updating start time of mission: {ex}"
            )

        return hub.acknowledge(message)

    async def _handle_MSN_START(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to start a mission as soon as possible.
        The handler will also authorize the mission to start if it is not
        authorized yet.
        """
        try:
            mission = self._get_mission_from_request_by_id(message)
        except RuntimeError as ex:
            return hub.reject(message, reason=str(ex))

        if mission.is_started:
            return hub.reject(message, reason="Mission already started")

        try:
            mission.start_now()
        except Exception as ex:
            return hub.reject(message, reason=f"Error while starting mission: {ex}")

        return hub.acknowledge(message)

    def _get_mission_from_request_by_id(self, message: FlockwaveMessage) -> Mission:
        id = message.body.get("id") or ""
        mission = self.find_mission_by_id(id)
        if mission is None:
            raise RuntimeError("No such mission")
        return mission

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
