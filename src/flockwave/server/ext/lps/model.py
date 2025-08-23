"""Types specific to the local positioning system support extension."""

from abc import ABC, abstractmethod
from blinker import Signal
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    Optional,
    TypeVar,
    final,
)

from flockwave.gps.vectors import PositionXYZ
from flockwave.server.model.battery import BatteryInfo
from flockwave.server.model.object import ModelObject

__all__ = ("LocalPositioningSystem", "LocalPositioningSystemType")


@dataclass
class Anchor:
    """Representation of a single anchor in a local positioning system (LPS)."""

    id: str
    """The ID of the anchor. Must be unique within a local positioning system,
    but two local positioning systems may have anchors with the same ID.
    """

    active: bool = True
    """Whether the anchor is active (i.e. online)."""

    position: Optional[PositionXYZ] = None
    """The position of the anchor in the coordinate system of the local
    positioning system, if known. ``None`` if not known or not applicable.
    """

    battery: Optional[BatteryInfo] = None
    """The battery information of the anchor; specifies its voltage, percentage
    and whether it is charging or not. ``None`` if the anchor has no battery.
    """

    def activate(self) -> bool:
        """Marks the given anchor as active.

        Returns:
            whether the anchor was inactive before this method call
        """
        if not self.active:
            self.active = True
            return True
        else:
            return False

    def deactivate(self) -> bool:
        """Marks the given anchor as inactive.

        Returns:
            whether the anchor was active before this method call
        """
        if self.active:
            self.active = False
            return True
        else:
            return False

    def ensure_battery_info(self) -> BatteryInfo:
        """Ensures that the anchor has battery information.

        If the anchor does not have battery information, it is initialized with
        default values.

        Returns:
            the battery information of the anchor
        """
        if self.battery is None:
            self.battery = BatteryInfo()
        return self.battery

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of the anchor."""
        return {
            "id": self.id,
            "active": bool(self.active),
            "position": self.position,
            "battery": self.battery,
        }


class LocalPositioningSystem(ModelObject):
    """Representation of a single local positioning system (LPS) on the server.

    A local positioning system consists of a _type_, an associated
    _configuration_, and a set of base stations that provide position information
    to objects using the services of the local positioning system.
    """

    _id: str = ""
    """The unique identifier of the LPS."""

    type: str = ""
    """The type of the LPS. Must be one of the identifiers from the LPS type
    registry.
    """

    name: str = ""
    """The name of the LPS that is to be displayed on user interfaces."""

    errors: list[int]
    """The list of error codes corresponding to the local positioning system."""

    anchors: list[Anchor]
    """The list of anchors corresponding to the local positioning system."""

    on_updated: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the state of the local positioning "
        "system changes in any way that clients might be interested in."
    )

    _schema_validator_getter: Optional[Callable[[], Callable[[Any], None]]] = None
    """Getter for the validator for the configuration parameters of the local
    positioning system, generated from the JSON schema of the LPS type.
    """

    def __init__(self) -> None:
        self.errors = []
        self.anchors = []

    @property
    def device_tree_node(self) -> None:
        return None

    @property
    def id(self) -> str:
        return self._id

    @property
    def json(self) -> dict[str, Any]:
        """Returns the JSON representation of the local positioning system."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "errors": self.errors,
            "anchors": self.anchors,
        }

    async def calibrate(self) -> None:
        """Performs a calibration of the local positioning system.

        Raises:
            NotImplementedError: if the local positioning system cannot be
                calibrated
            RuntimeError: when an error happens during calibration.
        """
        raise NotImplementedError

    @final
    def configure(self, cfg: dict[str, Any]) -> None:
        """Configures the local positioning system with the given parameters.

        This method is final; subclasses should override the `_configure_inner()`
        method instead to implement the actual configuration logic. Validation
        is taken care of by the `configure()` method, which checks that the
        given configuration parameters match the JSON schema returned by the
        `get_configuration_schema()` method of the type of the local positioning
        system.

        Parameters:
            cfg: a dictionary containing the configuration parameters for the
                local positioning system

        Raises:
            NotImplementedError: if the local positioning system cannot be
                configured
            RuntimeError: when an error happens during configuration.
        """
        import fastjsonschema

        if self._schema_validator_getter is None:
            raise RuntimeError("LPS configuration schema validator getter is not set")

        validator = self._schema_validator_getter()
        try:
            validator(cfg)
        except fastjsonschema.JsonSchemaException as e:
            raise RuntimeError(
                f"Invalid configuration for LPS {self.id!r}: {str(e)}"
            ) from e

        return self._configure_inner(cfg)

    def _configure_inner(self, cfg: dict[str, Any]) -> None:
        raise NotImplementedError

    def notify_updated(self) -> None:
        """Notifies all subscribers to the `on_updated()` event that the state
        of the local positioning system was updated.
        """
        self.on_updated.send(self)


T = TypeVar("T", bound=LocalPositioningSystem)
"""Type variable representing a subclass of LocalPositioningSystem_ that a given
LocalPositioningSystemType_ creates when asked to create a new instance.
"""


class LocalPositioningSystemType(Generic[T], ABC):
    """Base class for local positioning system (LPS) types.

    New LPS types in the Skybrush server may be implemented by deriving a class
    from this base class and then registering it in the LPS type registry.
    """

    @property
    @abstractmethod
    def description(self) -> str:
        """A longer, human-readable description of the LPS type that can be
        used by clients for presentation purposes.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """A human-readable name of the LPS type that can be used by
        clients for presentation purposes.
        """
        raise NotImplementedError

    @abstractmethod
    def create(self) -> T:
        """Creates a new instance with a default parameter set.

        Returns:
            a new LPS instance
        """
        raise NotImplementedError

    def describe(self) -> dict[str, str]:
        """Returns a JSON object that can be used to describe this LPS type
        in JSON messages between the server and the connected clients.
        """
        return {"name": self.name, "description": self.description}

    @abstractmethod
    def get_configuration_schema(self) -> dict[str, Any]:
        """Returns the JSON schema associated with general configuration
        parameters of instances of this LPS type.

        If you do not intend to use a schema, simply return an empty dictionary.
        Note that an empty dictionary is not a valid JSON schema; if you want to
        declare that you need no parameters, return ``{ "type": "object" }``
        instead.

        Returns:
            JSON schema of general LPS configuration parameters
        """
        raise NotImplementedError

    @final
    def get_configuration_schema_validator(self) -> Callable[[Any], None]:
        """Returns a validator for the configuration parameters of the LPS type.

        The validator is generated from the JSON schema returned by the
        `get_configuration_schema()` method of this LPS type.

        Returns:
            a validator that checks whether the given configuration parameters
            match the JSON schema of this LPS type
        """
        import fastjsonschema

        return fastjsonschema.compile(self.get_configuration_schema())  # type: ignore[return-value]
