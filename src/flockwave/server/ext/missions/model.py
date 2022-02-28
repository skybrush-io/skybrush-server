"""Types specific to the mission planning and management extension."""

from abc import ABCMeta, abstractmethod, abstractproperty
from dataclasses import dataclass, field
from time import time
from typing import (
    final,
    Any,
    Awaitable,
    ClassVar,
    Dict,
    Generic,
    Optional,
    Union,
    TypeVar,
)

from flockwave.server.utils import maybe_round

from .types import MissionState

__all__ = ("Mission", "MissionPlan", "MissionType")


@dataclass
class Mission:
    """Representation of a single mission on the server.

    A mission consists of a _type_, an associated set of _parameters_, an
    optional _mission plan_ (generated from the type and the parameters), and a
    set of drones that the server is allowed to control while the mission is
    running.
    """

    id: str = ""
    """The unique identifier of the mission."""

    type: str = ""
    """The type of the mission. Must be one of the identifiers from the mission
    type registry.
    """

    state: MissionState = MissionState.NEW
    """The state of the mission."""

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

    @property
    def is_finalized(self) -> bool:
        """Returns whether the mission is finalized."""
        return self.finalized_at is not None

    @property
    def json(self) -> Dict[str, Any]:
        """Returns the JSON representation of the mission."""
        return {
            "id": self.id,
            "type": self.type,
            "state": self.state.value,
            "parameters": self.parameters,
            "createdAt": maybe_round(self.created_at),
            "finalizedAt": maybe_round(self.finalized_at),
            "startsAt": maybe_round(self.starts_at),
            "startedAt": maybe_round(self.started_at),
            "finishedAt": maybe_round(self.finished_at),
        }

    @property
    def parameters(self) -> Dict[str, Any]:
        """Returns the parameters of the mission in the format they should be
        serialized when converting into JSON.

        Override this getter in subclasses if your mission uses custom
        parameters that you wish to expose in its JSON representation.
        """
        return {}

    def finalize(self) -> None:
        """Finalizes the mission, moving it from its ``NEW`` state to
        ``FINALIZED``. Parameters of finalized missions can not be
        modified any more.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        self._ensure_new()
        self.finalized_at = time()
        self.state = MissionState.FINALIZED

    @final
    def update_parameters(self, parameters: Dict[str, Any]) -> None:
        """Updates one or more parameters of the mission.

        This function must be called only when the mission is in the ``NEW``
        state.

        Do not override this method if you want to add support for mission
        parameters; update the internal `_update_parameters()` method instead.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        self._ensure_new()
        return self._update_parameters(parameters)

    def _ensure_new(self) -> None:
        """Ensures that the mission is in the ``NEW`` state.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        if self.state != MissionState.NEW:
            raise RuntimeError("Mission is already finalized")

    def _update_parameters(self, parameters: Dict[str, Any]) -> None:
        """Updates one or more parameters of the mission.

        This function _assumes_ that the mission is in the ``NEW`` state. It is
        illegal to call this function when the mission is in any other state.

        Override this method in subclasses if you want to add support for mission
        parameters. The default implementation does nothing, therefore it is
        safe to override without calling the superclass method.
        """
        pass


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


T = TypeVar("T", bound=Mission)
"""Type variable representing a subclass of Mission_ that a given MissionType_
creates when asked to create a new mission instance.
"""


class MissionType(Generic[T], metaclass=ABCMeta):
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
    def create_mission(self) -> T:
        """Creates a new mission with a default parameter set.

        Returns:
            a new mission instance
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
