from typing import Any, Dict

from .types import MissionPlan, MissionType

__all__ = ("LandImmediatelyMissionType",)


class LandImmediatelyMissionType(MissionType):
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

    def create_plan(self, parameters: Dict[str, Any]) -> MissionPlan:
        return MissionPlan.EMPTY
