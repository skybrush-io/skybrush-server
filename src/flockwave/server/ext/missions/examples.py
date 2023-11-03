from logging import Logger
from trio import sleep
from typing import Any, Optional

from .model import Mission, MissionType

__all__ = ("LandImmediatelyMissionType",)


class LandImmediatelyMission(Mission):
    """Example mission that lands all associated drones as soon as it gains
    control of the drone.

    This mission type is mostly for illustrative and testing purposes.
    """

    delay: float = 0.0
    """Number of seconds to wait before sending the landing command to a drone."""

    @Mission.parameters.getter
    def parameters(self) -> dict[str, Any]:
        return {"delay": self.delay}

    async def _run(self, log: Optional[Logger] = None) -> None:
        await sleep(30)

    def _update_parameters(self, parameters: dict[str, Any]) -> None:
        delay = parameters.get("delay")
        if delay is not None:
            self.delay = delay


class LandImmediatelyMissionType(MissionType[LandImmediatelyMission]):
    """Example mission type that lands all associate drones as soon as it
    gains control of the drone.

    This mission type is mostly for illustrative and testing purposes.
    """

    @property
    def description(self) -> str:
        return "Lands all associated UAVs as soon as possible."

    @property
    def name(self) -> str:
        return "Landing"

    def create_mission(self) -> LandImmediatelyMission:
        return LandImmediatelyMission()

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "delay": {
                    "description": "Number of seconds to wait before landing.",
                    "type": "number",
                    "inclusiveMinimum": 0,
                    "default": 0,
                }
            },
            "additionalProperties": False,
        }

    def get_plan_parameter_schema(self) -> dict[str, Any]:
        return {}
