"""Extension that provides some basic facilities for mission planning and
uploading missions to UAVs.
"""

from __future__ import annotations

from contextlib import ExitStack
from inspect import isawaitable
from trio import sleep_forever
from typing import Any, Dict, cast

from flockwave.server.ext.base import Extension
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import Client, FlockwaveMessage

from .registry import MissionPlannerRegistry
from .types import MissionPlan


class MissionManagementExtension(Extension):
    """Extension that provides some basic facilities for mission planning and
    uploading missions to UAVs.
    """

    _registry: MissionPlannerRegistry

    def __init__(self):
        super().__init__()
        self._registry = MissionPlannerRegistry()

    def exports(self) -> Dict[str, Any]:
        return {
            "use": self._registry.use,
        }

    async def run(self):
        assert self.app is not None

        with ExitStack() as stack:
            stack.enter_context(
                self.app.message_hub.use_message_handlers(
                    {"X-MSN-PLAN": self._handle_MSN_PLAN}
                )
            )
            await sleep_forever()

    async def _handle_MSN_PLAN(
        self, message: FlockwaveMessage, sender: Client, hub: MessageHub
    ):
        """Handles an incoming request to plan a mission using a given mission
        planner.
        """
        id = message.body.get("id")
        planner = self._registry.find_by_id(id) if isinstance(id, str) else None
        if planner is None:
            return hub.reject(message, reason="No such mission planner")

        parameters = message.body.get("parameters", {})
        if not isinstance(parameters, dict):
            return hub.reject(message, reason="Parameters must be a dictionary")

        maybe_plan = planner()
        if isawaitable(maybe_plan):
            plan = cast(MissionPlan, await maybe_plan)
        else:
            plan = cast(MissionPlan, maybe_plan)

        return {"result": plan}


construct = MissionManagementExtension
description = "Basic facilities for planning and uploading missions."
schema = {}
tags = "experimental"
