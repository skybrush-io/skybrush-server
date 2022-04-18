"""Types specific to the mission planning and management extension."""

from abc import ABCMeta, abstractmethod, abstractproperty
from logging import Logger
from blinker import Signal
from dataclasses import dataclass
from datetime import datetime
from time import time
from trio import Cancelled
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

from flockwave.server.model.object import ModelObject
from flockwave.server.utils import maybe_round

from .types import MissionState

__all__ = ("Mission", "MissionPlan", "MissionType")


class Mission(ModelObject):
    """Representation of a single mission on the server.

    A mission consists of a _type_, an associated set of _parameters_, an
    optional _mission plan_ (generated from the type and the parameters), and a
    set of drones that the server is allowed to control while the mission is
    running.

    This is an abstract superclass that is meant to serve as a base implementation
    for "real" missions. You will need to override at least the ``run()`` method
    in subclasses.
    """

    _id: str = ""
    """The unique identifier of the mission."""

    type: str = ""
    """The type of the mission. Must be one of the identifiers from the mission
    type registry.
    """

    state: MissionState = MissionState.NEW
    """The state of the mission."""

    created_at: float
    """Timestamp that stores when the mission was created on the server."""

    authorized_at: Optional[float] = None
    """Timestamp that stores when the mission was authorized to start on the
    server, or ``None`` if the mission was not authorized to start yet.
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

    log: Optional[Logger] = None
    """Logger to use during the execution of the mission. Not available when
    the mission is not running.
    """

    _cancel_requested: bool = False
    """Stores whether the mission was requested to be cancelled."""

    _is_allowed_to_start_late: bool = False
    """Stores whether the mission is allowed to start even if the current
    timestamp is later than the start time of the mission _when the mission
    is sent to the scheduler_. Typically this property can remain ``False``;
    the only time you need to set it to ``True`` is when you want the mission
    to start immediately. In that case, you can set the start time of the mission
    to the current POSIX timestamp, but you also need to set ``_is_allowed_to_start_late``
    to ``True``, otherwise a few milliseconds will pass until the mission gets
    scheduled in the scheduler, and by that time it would already be late.
    """

    on_authorization_changed: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the authorization of a mission changes."
    )

    on_cancel_requested: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the user requests the cancellation of the mission."
    )

    on_start_time_changed: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the start time of a mission changes."
    )

    on_updated: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the state of the mission changes in "
        "any way that clients might be interested in."
    )

    def __init__(self):
        """Constructor."""
        self.created_at = time()

    @property
    def cancel_requested(self) -> bool:
        """Returns whether the user requested the mission to be cancelled."""
        return self._cancel_requested

    @property
    def device_tree_node(self) -> None:
        return None

    @property
    def id(self) -> str:
        return self._id

    @property
    def is_allowed_to_start_late(self) -> bool:
        """Returns whether the mission is allowed to start later than its
        scheduled start time. This property is set internally to ``True``
        when ``start_now()`` is called.
        """
        return self._is_allowed_to_start_late

    @property
    def is_authorized_to_start(self) -> bool:
        """Returns whether the mission is authorized to start."""
        return self.authorized_at is not None

    @property
    def is_finished(self) -> bool:
        """Returns whether the mission has finished already (successfully or not)."""
        return self.finished_at is not None

    @property
    def is_started(self) -> bool:
        """Returns whether the mission has been started already."""
        return self.started_at is not None

    @property
    def json(self) -> Dict[str, Any]:
        """Returns the JSON representation of the mission."""
        return {
            "id": self.id,
            "type": self.type,
            "state": self.state.value,
            "parameters": self.parameters,
            "createdAt": maybe_round(self.created_at),
            "authorizedAt": maybe_round(self.authorized_at),
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

    @property
    def was_successful(self) -> bool:
        """Returns whether the mission finished successfully."""
        return self.state is MissionState.SUCCESSFUL

    @final
    def authorize_to_start(self) -> None:
        """Authorizes the mission to start, moving it from its ``NEW`` state to
        ``AUTHORIZED_TO_START``. Parameters of missions that are authorized to
        start can not be modified any more. The scheduled start time may still
        be modified or cleared.

        This function is a no-op if the mission is already authorized with an
        earlier timestamp.

        Do not override this method. If you want to react to changes in the
        authorization state of the mission, update the internal
        `_handle_authorization()` method instead.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` or
                ``AUTHORIZED_TO_START`` state
        """
        self._ensure_not_started_yet()
        old_authorization_time = self.authorized_at
        old_state = self.state

        if self.authorized_at is not None:
            self.authorized_at = min(self.authorized_at, time())
        else:
            self.authorized_at = time()

        if old_state != self.state or old_authorization_time != self.authorized_at:
            self.state = MissionState.AUTHORIZED_TO_START
            self._handle_authorization()
            self.on_authorization_changed.send(self)
            self.notify_updated()

    def cancel(self) -> None:
        """Cancels the mission if it is running. Skips execution and moves
        straight to the cancelled state if the mission is not running. No-op if
        the mission was already cancelled or terminated.
        """
        self._cancel_requested = True
        if not self.is_started:
            # Start the mission now; the scheduler will call the run() method
            # of the mission, which will return immediately and update the
            # state variables
            self.start_now()
        self.on_cancel_requested.send(self)

    def notify_updated(self) -> None:
        """Notifies all subscribers to the `on_updated()` event that the mission
        was updated.
        """
        self.on_updated.send(self)

    @final
    def revoke_authorization(self) -> None:
        """Revokes the authorization of the mission to start, moving it from
        its ``AUTHORIZED_TO_START`` state back to ``NEW``. Parameters can then
        be modified again. The scheduled start time will not change but the
        mission will not start until authorized again.

        This function is a no-op if the mission is not authorized to start yet.

        Do not override this method. If you want to react to changes in the
        authorization state of the mission, update the internal
        `_handle_authorization()` method instead.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` or
                ``AUTHORIZED_TO_START`` state
        """
        self._ensure_not_started_yet()
        if self.state != MissionState.NEW or self.authorized_at is not None:
            self.authorized_at = None
            self.state = MissionState.NEW
            self._handle_authorization()
            self.on_authorization_changed.send(self)
            self.notify_updated()

    @final
    async def run(self, log: Optional[Logger]) -> None:
        """Run the task corresponding to the mission. This function will be
        called by the mission scheduler when it is time to start the mission.

        This function must be called only when the mission is in the
        ``AUTHORIZED_TO_START`` state (and the scheduler will ensure this in
        normal operation).

        Do not override this method; override the internal `_run()` method
        instead.

        Parameters:
            log: the logger that the mission should use to log events of interest

        Raises:
            RuntimeError: if the mission is not in the ``AUTHORIZED_TO_START``
                state
        """
        self._ensure_waiting_to_start()

        self.started_at = time()

        if not self._cancel_requested:
            self.state = MissionState.ONGOING
            self.notify_updated()

        self.log = log
        try:
            if not self._cancel_requested:
                await self._run()
        except RuntimeError as ex:
            # Sort-of-expected exception, simply log it as an error
            if log:
                log.error(str(ex), extra={"id": self.id, "telemetry": "ignore"})
            self.state = MissionState.FAILED
        except Exception:
            # More serious exception, log it as a proper exception
            if log:
                log.exception(
                    "Unexpected error while executing mission", extra={"id": self.id}
                )
            self.state = MissionState.FAILED
        except Cancelled:
            # Scheduler cancelled the execution of the mission
            if log:
                log.warn("Mission cancelled", extra={"id": self.id})
            self.state = MissionState.CANCELLED

            # Cancelled exceptions must be re-raised
            raise
        else:
            if self._cancel_requested:
                self.state = MissionState.CANCELLED
            else:
                self.state = MissionState.SUCCESSFUL
        finally:
            self.log = None
            self.finished_at = time()
            self.notify_updated()

    @final
    def start_now(self) -> None:
        """Authorizes the mission to start and starts it as soon as possible.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` or
                ``AUTHORIZED_TO_START`` states.
        """
        self._ensure_not_started_yet()
        self._is_allowed_to_start_late = True
        self.update_start_time(time())
        self.authorize_to_start()

    @final
    def update_parameters(self, parameters: Dict[str, Any]) -> None:
        """Updates one or more parameters of the mission.

        This function must be called only when the mission is in the ``NEW``
        state.

        Do not override this method. If you want to add support for mission
        parameters, update the internal `_update_parameters()` method instead.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        self._ensure_new()
        self._update_parameters(parameters)
        self.notify_updated()

    @final
    def update_start_time(self, start_time: Optional[Union[float, datetime]]):
        """Updates or clears the start time of the missions.

        This function must be called only when the mission is in the ``NEW``
        or ``AUTHORIZED_TO_START`` state.

        Do not override this method. If you want to react to changes in the
        scheduled start time of the mission, update the internal
        `_handle_start_time_change()` method instead.
        """
        self._ensure_not_started_yet()
        timestamp = (
            start_time
            if start_time is None or isinstance(start_time, float)
            else start_time.timestamp()
        )

        if self.starts_at != timestamp:
            self.starts_at = timestamp
            self._handle_start_time_change()
            self.on_start_time_changed.send(self)
            self.notify_updated()

    def _ensure_new(self) -> None:
        """Ensures that the mission is in the ``NEW`` state.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        if self.state != MissionState.NEW:
            raise RuntimeError("Mission is already finalized")

    def _ensure_not_started_yet(self) -> None:
        """Ensure that the mission has not started yet. This is the case if the
        mission is in the ``NEW`` or ``AUTHORIZED_TO_START`` state.

        Raises:
            RuntimeError: if the mission has already started
        """
        if self.state not in (MissionState.NEW, MissionState.AUTHORIZED_TO_START):
            raise RuntimeError("Mission has started already")

    def _ensure_waiting_to_start(self) -> None:
        """Ensures that the mission is in the ``AUTHORIZED_TO_START`` state.

        Raises:
            RuntimeError: if the mission is not in the ``NEW`` state
        """
        if self.state != MissionState.AUTHORIZED_TO_START:
            raise RuntimeError("Mission is not in the 'authorized to start' state")

    def _handle_authorization(self) -> None:
        """Handles the event when the authorization state of the mission changes.

        Override this method in subclasses if you want to react to changes in
        the authorization state of the mission. The default implementation does
        nothing, therefore it is safe to override without calling the superclass
        method.
        """
        pass

    def _handle_start_time_change(self) -> None:
        """Handles the event when the scheduled start time of the mission
        changes.

        Override this method in subclasses if you want to react to changes in
        the scheduled start time of the mission. The default implementation does
        nothing, therefore it is safe to override without calling the superclass
        method.
        """
        pass

    @abstractmethod
    async def _run(self, log: Optional[Logger] = None) -> None:
        """Async function that runs the task corresponding to the mission.
        This function must be overridden in subclasses to add your own behaviour.

        Parameters:
            log: the logger that the mission should use to log events of interest
        """
        raise NotImplementedError

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
