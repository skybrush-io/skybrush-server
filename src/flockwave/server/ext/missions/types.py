"""Types specific to the mission planning and management extension."""

from abc import ABCMeta, abstractmethod, abstractproperty
from dataclasses import dataclass, field
from time import time
from typing import Any, Awaitable, ClassVar, Dict, Optional, Union

__all__ = ("MissionPlan", "MissionState", "MissionType")


@dataclass
class MissionState:
    """Representation of the state of a single mission within the server.

    A mission consists of a _type_, an associated set of _parameters_, an
    optional _mission plan_ (generated from the type and the parameters), and a
    set of drones that the server is allowed to control while the mission is
    running.
    """

    id: str
    """The unique identifier of the mission."""

    type: str
    """The type of the mission. Must be one of the identifiers from the mission
    type registry.
    """

    parameters: Dict[str, Any] = field(default_factory=dict)
    """The parameters of the mission. They may be used directly by the server
    when the mission is being executed, or they may also be used to generate a
    mission plan that the client may display before the user starts the mission.
    """

    created_at: float = field(default_factory=time)
    """Timestamp that stores when the mission was created on the server."""

    finalized_at: Optional[float] = None
    """Timestamp that stores when the mission was finalized on the server, or
    ``None`` if the mission was not finalized yet.
    """

    starts_at: Optional[float] = None
    """Scheduled start time of the mission (if it is scheduled to start
    automatically). ``None`` if there is no scheduled start time yet.
    """

    started_at: Optional[float] = None
    """Timestamp that stores when the mission has started, or ``None`` if the
    mission has not started yet.
    """

    finished_at: Optional[float] = None
    """Timestamp that stores when the mission has finished, or ``None`` if it
    has not finished yet or has not been started yet.
    """


@dataclass
class MissionPlan:
    """Simple dataclass to model the object that a mission planner should
    return.
    """

    format: str
    """Format of the returned mission. This may indicate a Skybrush show
    file, an ArduPilot-style mission or anything else. It is the responsibility
    of the client to handle the payload depending on the format supplied here.
    """

    payload: Any = None
    """The actual mission description. For Skybrush shows, this can be the
    JSON representation of the show. For ArduPilot-style missions, this can be
    a string in the standard textual mission format supported by Mission Planner
    or QGroundControl.

    It is assumed that the payload is JSON-serializable.
    """

    EMPTY: ClassVar["MissionPlan"]
    """Default, "empty" mission plan that can be returned if no specific plan
    can be created for a mission. Useful for self-organized missions.
    """

    @property
    def json(self):
        """Returns the JSON representation of the mission plan."""
        return {"format": self.format, "payload": self.payload}


MissionPlan.EMPTY = MissionPlan(format="empty")


class MissionType(metaclass=ABCMeta):
    """Base class for mission types, i.e. classes that define how to create a
    mission plan to perform a given task and how to execute such a plan.

    New types of missions in the Skybrush server may be implemented by deriving
    a class from this base class and then registering it in the mission type
    registry.
    """

    @abstractproperty
    def description(self) -> str:
        """A longer, human-readable description of the mission type that can be
        used by clients for presentation purposes.
        """
        raise NotImplementedError

    @abstractproperty
    def name(self) -> str:
        """A human-readable name of the mission type that can be used by
        clients for presentation purposes.
        """
        raise NotImplementedError

    @abstractmethod
    def create_plan(
        self, parameters: Dict[str, Any]
    ) -> Union[MissionPlan, Awaitable[MissionPlan]]:
        """Creates a new mission plan with the given parameters.

        Parameters:
            parameters: the parameters of the mission

        Returns:
            a mission plan or an awaitable that resolves to a mission plan in
            an asynchronous manner
        """
        raise NotImplementedError
