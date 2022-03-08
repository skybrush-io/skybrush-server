from logging import Logger
from trio import sleep
from typing import Any, Dict, Optional

from .model import Mission, MissionPlan, MissionType

__all__ = ("LandImmediatelyMissionType",)


class LandImmediatelyMission(Mission):
    """Example mission that lands all associated drones as soon as it gains
    control of the drone.

    This mission type is mostly for illustrative and testing purposes.
    """

    delay: float = 0.0
    """Number of seconds to wait before sending the landing command to a drone."""

    @Mission.parameters.getter
    def parameters(self) -> Dict[str, Any]:
        return {"delay": self.delay}

    async def _run(self, log: Optional[Logger] = None) -> None:
        await sleep(30)

    def _update_parameters(self, parameters: Dict[str, Any]) -> None:
        delay: float = parameters.get("delay", 0.0)
        if not isinstance(delay, (int, float)):
            raise RuntimeError("delay must be numeric")
        self.delay = float(delay)


class LandImmediatelyMissionType(MissionType[LandImmediatelyMission]):
    """Example mission type that lands all associate drones as soon as it
    gains control of the drone.

    This mission type is mostly for illustrative and testing purposes.
    """

    @property
    def description(self) -> str:
        return "This mission lands all associated UAVs as soon as possible."

    @property
    def name(self) -> str:
        return "Landing"

    def create_mission(self) -> LandImmediatelyMission:
        return LandImmediatelyMission()

    def create_plan(self, parameters: Dict[str, Any]) -> MissionPlan:
        return MissionPlan.EMPTY
