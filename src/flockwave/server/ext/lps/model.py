"""Types specific to the local positioning system support extension."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Literal, cast, final

from blinker import Signal
from flockwave.gps.vectors import PositionXYZ
from typing_extensions import TypeVar

from flockwave.server.model import BatteryInfo, ErrorSet, ModelObject, Quaternion
from flockwave.server.model.commands import ProgressEventsWithSuspension

__all__ = (
    "Anchor",
    "LocalPositioningSystem",
    "LocalPositioningSystemFeature",
    "LocalPositioningSystemType",
)


LocalPositioningSystemFeature = Literal["calibrate", "configure"]
"""Type alias for the optional features that a local positioning system (LPS)
may support.
"""


@dataclass
class Anchor:
    """Representation of a single anchor in a local positioning system (LPS)."""

    id: str
    """The ID of the anchor. Must be unique within a local positioning system,
    but two local positioning systems may have anchors with the same ID.
    """

    active: bool = True
    """Whether the anchor is active (i.e. online)."""

    position: PositionXYZ | None = None
    """The position of the anchor in the coordinate system of the local
    positioning system, if known. ``None`` if not known or not applicable.
    """

    attitude: Quaternion | None = None
    """The attitude of the anchor in the coordinate system of the local
    positioning system, if known. ``None`` if not known or not applicable.

    The attitude is the rotation from the coordinate system of the anchor to
    the coordinate system of the local positioning system. In the coordinate
    system of the anchor, the X axis points forward, out of the front face of
    the anchor, and the Z axis points up. An anchor with an identity attitude
    faces the positive X axis of the local positioning system.
    """

    battery: BatteryInfo | None = None
    """The battery information of the anchor; specifies its voltage, percentage
    and whether it is charging or not. ``None`` if the anchor has no battery.
    """

    errors: ErrorSet = field(default_factory=ErrorSet)
    """The error codes of the anchor.

    The error codes are the same as the ones used for UAVs and other objects
    in the Flockwave protocol. Their severity is determined by their value:
    codes below 64 are informational, codes below 128 are warnings, codes
    below 192 are errors, and the remaining codes are critical errors.
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
            "attitude": list(self.attitude) if self.attitude is not None else None,
            "battery": self.battery,
            "errors": self.errors,
        }


C = TypeVar("C", bound=Mapping[str, Any], default=dict[str, Any])
"""Type variable representing the type of the configuration object of a local
positioning system (LPS).
"""


class LocalPositioningSystem(ModelObject, Generic[C]):
    """Representation of a single local positioning system (LPS) on the server.

    A local positioning system consists of a _type_, an associated
    _configuration_, and a set of base stations that provide position information
    to objects using the services of the local positioning system.

    The class is generic over the type of the configuration object. Subclasses
    should specify a `TypedDict` that matches the JSON schema of the
    configuration. Code that handles arbitrary local positioning systems should
    use ``LocalPositioningSystem[Any]``.
    """

    _id: str = ""
    """The unique identifier of the LPS."""

    type: str = ""
    """The type of the LPS. Must be one of the identifiers from the LPS type
    registry.
    """

    name: str = ""
    """The name of the LPS that is to be displayed on user interfaces."""

    errors: ErrorSet
    """The list of error codes corresponding to the local positioning system."""

    anchors: list[Anchor]
    """The list of anchors corresponding to the local positioning system."""

    on_updated: ClassVar[Signal] = Signal(
        doc="Signal that is emitted when the state of the local positioning "
        "system changes in any way that clients might be interested in."
    )

    _schema_validator_getter: Callable[[], Callable[[Any], None]] | None = None
    """Getter for the validator for the configuration parameters of the local
    positioning system, generated from the JSON schema of the LPS type.
    """

    def __init__(self) -> None:
        self.errors = ErrorSet()
        self.anchors = []

    @property
    def device_tree_node(self) -> None:
        return None

    @property
    def features(self) -> Sequence[LocalPositioningSystemFeature]:
        """The optional features supported by the local positioning system.

        The default implementation reports ``calibrate`` if the subclass
        overrides `calibrate()`, and ``configure`` if the subclass overrides
        `_configure_inner()`. The ``configure`` feature means that clients may
        both read and update the configuration. Override this property if the
        set of supported features depends on the state of the instance.
        """
        cls = type(self)
        features: list[LocalPositioningSystemFeature] = []
        if cls.calibrate is not LocalPositioningSystem.calibrate:
            features.append("calibrate")
        if cls._configure_inner is not LocalPositioningSystem._configure_inner:
            features.append("configure")
        return features

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
            "features": self.features,
        }

    def calibrate(
        self, params: Mapping[str, Any]
    ) -> ProgressEventsWithSuspension[Any, Any]:
        """Performs a calibration of the local positioning system.

        Subclasses that support calibration must implement this method as an
        async generator. The generator may yield `Progress` objects to report
        progress, and `Suspend` objects to wait for the user, e.g., to move a
        drone to the next calibration point. The value that the client sends
        when it resumes the calibration is returned from the ``yield``
        expression. Any other object yielded from the generator is considered
        as the result of the calibration.

        The server cancels the calibration if it does not yield anything for
        30 seconds while not suspended. Long-running calibrations must yield
        progress reports regularly.

        Parameters:
            params: the calibration parameters sent by the client. The
                implementation is responsible for validating them.

        Raises:
            NotImplementedError: if the local positioning system cannot be
                calibrated
            RuntimeError: when the calibration parameters are invalid or when
                an error happens during calibration
        """
        raise NotImplementedError("This local positioning system cannot be calibrated")

    @final
    async def configure(self, cfg: Mapping[str, Any]) -> None:
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
            RuntimeError: if the configuration does not match the schema of the
                type of the local positioning system, or when an error happens
                during configuration
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

        await self._configure_inner(cast(C, cfg))

    async def _configure_inner(self, cfg: C) -> None:
        """Applies the given, already validated configuration parameters to
        the local positioning system.

        Parameters:
            cfg: a dictionary containing the configuration parameters for the
                local positioning system

        Raises:
            NotImplementedError: if the local positioning system cannot be
                configured
            RuntimeError: when an error happens during configuration.
        """
        raise NotImplementedError("This local positioning system cannot be configured")

    async def get_configuration(self) -> C:
        """Returns the current configuration of the local positioning system.

        The returned configuration must match the JSON schema returned by the
        `get_configuration_schema()` method of the type of the local positioning
        system. Subclasses that override `_configure_inner()` should override
        this method as well.

        Returns:
            the current configuration parameters of the local positioning system

        Raises:
            NotImplementedError: if the configuration of the local positioning
                system cannot be retrieved
            RuntimeError: when an error happens while retrieving the
                configuration
        """
        raise NotImplementedError(
            "The configuration of this local positioning system cannot be retrieved"
        )

    def notify_updated(self) -> None:
        """Notifies all subscribers to the `on_updated()` event that the state
        of the local positioning system was updated.
        """
        self.on_updated.send(self)


T = TypeVar("T", bound=LocalPositioningSystem[Any])
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

        If you do not intend to validate the configuration, return an empty
        dictionary. An empty dictionary is a valid JSON schema that accepts any
        configuration.

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

        return fastjsonschema.compile(self.get_configuration_schema())
