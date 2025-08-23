"""Model classes related to a single UAV."""

from __future__ import annotations

from abc import ABC, abstractmethod
from inspect import isawaitable
from typing import (
    Any,
    Awaitable,
    Callable,
    Generic,
    Iterable,
    Optional,
    TypedDict,
    Union,
    TypeVar,
    TYPE_CHECKING,
)

from flockwave.gps.vectors import GPSCoordinate, PositionXYZ, VelocityNED, VelocityXYZ
from flockwave.server.errors import NotSupportedError
from flockwave.server.logger import log as base_log
from flockwave.spec.schema import get_complex_object_schema

from flockwave.server.model.commands import Progress, ProgressEvents

from .attitude import Attitude
from .battery import BatteryInfo
from .devices import ObjectNode
from .gps import GPSFix, GPSFixLike
from .log import FlightLogMetadata
from .metamagic import ModelMeta
from .mixins import TimestampLike, TimestampMixin
from .object import ModelObject, register
from .preflight import PreflightCheckInfo
from .transport import TransportOptions
from .utils import as_base64, scaled_by

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

__all__ = (
    "is_uav",
    "PassiveUAVDriver",
    "UAV",
    "UAVBase",
    "UAVDriver",
    "UAVStatusInfo",
)

log = base_log.getChild("uav")


VersionInfo = dict[str, str]
"""Type alias for version information objects returned from UAVDriver, mapping
component names to version numbers
"""

TUAV = TypeVar("TUAV", bound="UAV")
"""Type variable that represents a UAV object."""

TDriver = TypeVar("TDriver", bound="UAVDriver")
"""Type variable that represents a UAV driver object."""

TResult = TypeVar("TResult")
"""Type variable that represents some unspecified result object."""


class BulkParameterUploadResponse(TypedDict, total=False):
    """Typed dictionary that is returned as a response for a PRM-SET-MANY
    request.
    """

    success: bool
    """Whether the bulk parameter upload succeeded. Always present."""

    failed: list[str]
    """List of parameter names for which the bulk upload failed. Omitted if
    the caller cannot provide the exact list of parameter names where the
    upload failed.
    """


class UAVStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single
    UAV.
    """

    class __meta__:
        schema = get_complex_object_schema("uavStatusInfo")
        mappers = {"heading": scaled_by(10), "debug": as_base64}

    debug: bytes
    errors: list[int]
    gps: GPSFix
    heading: float
    attitude: Optional[Attitude]
    id: str
    light: int
    mode: str
    position: GPSCoordinate
    positionXYZ: Optional[PositionXYZ]
    velocity: VelocityNED
    velocityXYZ: Optional[VelocityXYZ]
    battery: BatteryInfo
    rssi: list[int]

    def __init__(
        self, id: Optional[str] = None, timestamp: Optional[TimestampLike] = None
    ):
        """Constructor.

        Parameters:
            id: ID of the UAV
            timestamp: time when the status information was received. ``None``
                means to use the current date and time. Integers represent
                milliseconds elapsed since the UNIX epoch.
        """
        TimestampMixin.__init__(self, timestamp)

        self.debug = b""
        self.errors = []
        self.gps = GPSFix()
        self.heading = 0.0
        self.attitude = None
        self.id = id  # type: ignore
        self.light = 0  # black
        self.mode = ""
        self.position = GPSCoordinate()
        self.velocity = VelocityNED()
        self.positionXYZ = None
        self.velocityXYZ = None
        self.battery = BatteryInfo()
        self.rssi = []

    @property
    def position_xyz(self) -> Optional[PositionXYZ]:
        return self.positionXYZ

    @position_xyz.setter
    def position_xyz(self, value: Optional[PositionXYZ]) -> None:
        self.positionXYZ = value

    @property
    def velocity_xyz(self) -> Optional[VelocityXYZ]:
        return self.velocityXYZ

    @velocity_xyz.setter
    def velocity_xyz(self, value: Optional[VelocityXYZ]) -> None:
        self.velocityXYZ = value


@register("uav")
class UAV(ModelObject, ABC):
    """Abstract object that defines the interface of objects representing
    UAVs.
    """

    @property
    @abstractmethod
    def driver(self) -> "UAVDriver":
        """Returns the UAVDriver_ object that is responsible for handling
        communication with this UAV.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def id(self) -> str:
        """A unique identifier for the UAV, assigned at construction
        time.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def status(self) -> UAVStatusInfo:
        """Returns an UAVStatusInfo_ object representing the status of the
        UAV.
        """
        raise NotImplementedError


class UAVBase(UAV, Generic[TDriver]):
    """Base object for UAV implementations. Provides a default implementation
    of the methods required by the UAV_ interface.
    """

    def __init__(self, id: str, driver: TDriver):
        """Constructor.

        Parameters:
            id: the unique identifier of the UAV
            driver: the driver that is responsible for handling communication
                with this UAV.
        """
        self._device_tree_node = ObjectNode()
        self._driver = driver
        self._id = id
        self._status = UAVStatusInfo(id=id)
        self._initialize_device_tree_node(self._device_tree_node)

    @property
    def device_tree_node(self) -> ObjectNode:
        """Returns the ObjectNode object that represents the root of the
        device tree corresponding to the UAV.

        Returns:
            the node in the device tree where the subtree of the devices and
            channels of the UAV is rooted
        """
        return self._device_tree_node

    @property
    def driver(self) -> TDriver:
        """Returns the UAVDriver_ object that is responsible for handling
        communication with this UAV.
        """
        return self._driver

    @property
    def id(self) -> str:
        """A unique identifier for the UAV, assigned at construction
        time.
        """
        return self._id

    @property
    def status(self) -> UAVStatusInfo:
        """Returns an UAVStatusInfo_ object representing the status of the
        UAV.

        This property should be manipulated via the ``update_status()``
        method.
        """
        return self._status

    def _initialize_device_tree_node(self, node: ObjectNode) -> None:
        """Initializes the device tree node of the UAV when it is
        constructed.

        This method will be called from the constructor. Subclasses may
        override this method to provide a set of default devices for the
        UAV.

        Parameters:
            node: the tree node whose subtree this call should initialize
        """
        pass

    def clear_errors(self) -> None:
        """Clears the error codes of the UAV."""
        return self.update_status(errors=())

    def clear_errors_up_to_and_including(self, code: int) -> None:
        """Clears all the error codes of the UAV that are less than or equal
        to the given error code.
        """
        if self._status.errors:
            self.update_status(errors=(x for x in self._status.errors if x > code))

    def convert_ahl_to_amsl(
        self, altitude: float, *, current_ahl: Optional[float] = None
    ) -> float:
        """Converts an altitude given as altitude above home level to altitude
        above mean sea level.

        This function requires the drone to know its current AHL and AMSL so it
        can calculate an offset between them. Alternatively, if the `current_ahl`
        argument is not `None`, the given value is used as the current AHL.

        Returns:
            the given AHL altitude converted to AMSL

        Raises:
            RuntimeError: if the position of the UAV is not known yet
        """
        if self._status is None:
            raise RuntimeError("UAV status not known yet")

        # TODO(ntamas): maybe we should use the position only if it has been
        # updated recently. Or maybe not.

        pos = self._status.position
        if pos is None:
            raise RuntimeError(
                "Cannot convert AHL to AMSL, current position not known yet"
            )

        if pos.amsl is None:
            raise RuntimeError("Cannot convert AHL to AMSL, current AMSL not known yet")

        ahl = current_ahl if current_ahl is not None else pos.ahl
        if ahl is None:
            raise RuntimeError("Cannot convert AHL to AMSL, current AHL not known yet")

        return altitude - ahl + pos.amsl

    def ensure_error(self, code: int, present: bool = True) -> None:
        """Ensures that the given error code is present (or not present) in the
        error code list.

        This function does _not_ update the timestamp of the status information;
        you need to do it on your own by calling `update_status()`.

        Parameters:
            code: the code to add or remove
            present: whether to add the code (True) or remove it (False)
        """
        # If the error code is to be cleared and we don't have any errors
        # (which is the common code path), we can bail out immediately.
        if present or self._status.errors:
            code = int(code)

            if code in self._status.errors:
                if not present:
                    self._status.errors.remove(code)
            else:
                if present:
                    self._status.errors.append(code)

    def ensure_errors(self, codes: dict[int, bool]) -> None:
        """Updates multiple error codes with a single function call.

        Parameters:
            codes: dictionary mapping error codes to a boolean specifying
                whether the error code should be present or absent
        """
        if self._status.errors or any(present for present in codes.values()):
            for code, present in codes.items():
                self.ensure_error(code, present)

    def update_rssi(self, *, index: int, value: Optional[int] = None) -> None:
        """Updates the RSSI value of the UAV for the channel with the given
        index.

        Parameters:
            index: the index of the channel
            value: the new RSSI value in the range 0-100; -1 means "unknown",
                and so is ``None``.
        """
        value = min(100, max(-1, int(value))) if value is not None else -1
        rssi = self._status.rssi
        if len(rssi) <= index:
            rssi.extend([-1] * (index - len(rssi) + 1))
        rssi[index] = value
        self._status.update_timestamp()

    def update_status(
        self,
        *,
        position: Optional[GPSCoordinate] = None,
        position_xyz: Optional[PositionXYZ] = None,
        velocity: Optional[VelocityNED] = None,
        velocity_xyz: Optional[VelocityXYZ] = None,
        heading: Optional[float] = None,
        attitude: Optional[Attitude] = None,
        mode: Optional[str] = None,
        gps: Optional[GPSFixLike] = None,
        battery: Optional[BatteryInfo] = None,
        light: Optional[int] = None,
        errors: Optional[Union[int, Iterable[int]]] = None,
        debug: Optional[bytes] = None,
        rssi: Optional[Union[int, Iterable[int]]] = None,
    ):
        """Updates the status information of the UAV.

        Parameters with values equal to ``None`` are ignored.

        Parameters:
            position: the global (GPS) position of the UAV. It will be cloned to
                ensure that modifying this position object from the caller will
                not affect the UAV itself.
            position_xyz: the position of the UAV in some local coordinate system.
                It will be cloned to ensure that modifying this position object
                from the caller will not affect the UAV itself.
            velocity: the global (NED) velocity of the UAV. It will be cloned to
                ensure that modifying this velocity object from the caller will
                not affect the UAV itself.
            velocity_xyz: the velocity of the UAV in some local coordinate system.
                It will be cloned to ensure that modifying this position object
                from the caller will not affect the UAV itself.
            heading: the heading of the UAV, in degrees.
            attitude: the attitude (roll, pitch, yaw) of the UAV, in degrees.
            mode: the flight mode that the UAV is currently operating in
            gps: information about the GPS fix of the UAV
            battery: information about the status of the battery on the UAV.
                It will be cloned to ensure that modifying this object from
                the caller will not affect the UAV itself.
            light: the color of the primary light of the UAV, in RGB565
                encoding.
            errors: the error code or error codes of the UAV; use an empty list
                or tuple if the UAV has no errors
            debug: additional debug information to store
            rssi: the measured RSSI values for each of the channels the UAV is
                accessible on.
        """
        if position is not None:
            self._status.position.update_from(position, precision=7)
        if position_xyz is not None:
            if self._status.position_xyz is None:
                self._status.position_xyz = PositionXYZ()
            self._status.position_xyz.update_from(position_xyz, precision=3)
        if heading is not None:
            # Heading is rounded to 2 digits; it is unlikely that more
            # precision is needed and it saves space in the JSON
            # representation
            self._status.heading = round(heading % 360, 2)
        if attitude is not None:
            if self._status.attitude is None:
                self._status.attitude = Attitude()
            self._status.attitude.update_from(attitude)
        if velocity is not None:
            self._status.velocity.update_from(velocity, precision=2)
        if velocity_xyz is not None:
            if self._status.velocity_xyz is None:
                self._status.velocity_xyz = VelocityXYZ()
            self._status.velocity_xyz.update_from(velocity_xyz, precision=2)
        if mode is not None:
            self._status.mode = mode
        if battery is not None:
            self._status.battery.update_from(battery)
        if light is not None:
            self._status.light = int(light)
        if errors is not None:
            if isinstance(errors, int):
                errors = [errors] if errors > 0 else []
            else:
                errors = sorted(code for code in errors if code > 0)
            self._status.errors = errors
        if rssi is not None:
            if isinstance(rssi, int):
                rssi = [rssi]
            else:
                rssi = list(rssi)
            self._status.rssi = rssi
        if gps is not None:
            self._status.gps.update_from(gps)
        if debug is not None:
            self._status.debug = debug
        self._status.update_timestamp()


class UAVDriver(Generic[TUAV], ABC):
    """Interface specification for UAV drivers that are responsible for
    handling communication with a given group of UAVs via a common
    communication channel (e.g., a radio or a wireless network).

    Many of the methods in this class take a list of UAVs as an argument.
    These lists contain the UAVs to address with a specific request from
    the server, and it is the responsibility of the driver to translate
    these requests to actual commands that the UAVs understand, and
    transmit these commands to the UAVs. Implementors of methods receiving
    a list of UAVs may reasonably assume that all the UAVs are managed by
    this driver; it is the responsibility of the caller to ensure this.
    The UAV lists are assumed to contain UAVs with unique IDs only.

    These methods return a dictionary mapping UAVs to the results of
    executing the operation on the UAV. The result should be ``True`` if
    the operation succeeded, an object of type CommandExecutionStatus_ if
    the operation has started executing but has not been finished yet;
    anything else means failure. Failures should be denoted by strings
    explaining the reason of the failure.

    It is the responsibility of the implementor of these methods to ensure
    that all the UAVs that appeared in the input UAV list are also mentioned
    in the dictionary that is returned from the method.
    """

    app: "SkybrushServer"
    """The Skybrush server application that hosts the driver."""

    @staticmethod
    def _execute(func, *args, **kwds):
        """Executes the given function with the given positional and keyword
        arguments. When the function throws an exception, catches the exception
        and returns it as an object instead of raising it. When the function is
        asynchronous, returns the awaitable returned by the function.
        """
        try:
            return func(*args, **kwds)
        except Exception as ex:
            return ex

    def __init__(self):
        """Constructor."""
        self.app = None  # type: ignore

    def calibrate_component(self, uavs: list[TUAV], component: str):
        """Asks the driver to calibrate the given component on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_calibrate_component_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "calibration request",
            self._calibrate_component_single,
            component=component,
        )

    def enter_low_power_mode(
        self, uavs: list[TUAV], transport: Optional[TransportOptions] = None
    ):
        """Asks the driver to send a signal to the given UAVs to enter low-power
        mode. Each of the UAVs are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_enter_low_power_mode_single()`` and
        optionally ``_enter_low_power_mode_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "low-power mode request",
            self._enter_low_power_mode_single,
            getattr(self, "_enter_low_power_mode_broadcast", None),
            transport=transport,
        )

    def get_log(self, uav: TUAV, log_id: str):
        """Asks the driver to retrieve the log with the given ID from the
        given UAV.

        Returns:
            the log contents along with its metadata
        """
        raise NotImplementedError

    def get_log_list(self, uavs: list[TUAV]):
        """Asks the driver to retrieve the list of available logs from the
        given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_get_log_list_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs, "log listing request", self._get_log_list_single
        )

    def get_parameter(self, uavs: list[TUAV], name: str):
        """Asks the driver to retrieve the current value of a parameter from
        the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_get_parameter_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs, "parameter retrieval", self._get_parameter_single, name=name
        )

    def request_preflight_report(self, uavs: list[TUAV]):
        """Asks the driver to request a detailed report about the status of
        preflight checks on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_request_preflight_report_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "preflight report request",
            self._request_preflight_report_single,
        )

    def request_version_info(self, uavs: list[TUAV]):
        """Asks the driver to request detailed version information from the
        given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_request_version_info_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs, "version info request", self._request_version_info_single
        )

    def resume_from_low_power_mode(
        self, uavs: list[TUAV], transport: Optional[TransportOptions] = None
    ):
        """Asks the driver to send a signal to the given UAVs to resume normal
        operation from low-power mode. Each of the UAVs are assumed to be
        managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_resume_from_low_power_mode_single()`` and
        optionally ``_resume_from_low_power_mode_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "wakeup request",
            self._resume_from_low_power_mode_single,
            getattr(self, "_resume_from_low_power_mode_broadcast", None),
            transport=transport,
        )

    def send_command(self, uavs: list[TUAV], command: str, args=None, kwds=None):
        """Asks the driver to send a direct command to the given UAVs, each
        of which are assumed to be managed by this driver.

        The default implementation of this method passes on each command
        to the ``handle_multi_command_{command}()`` method where ``{command}``
        is replaced by the command argument. The method will be called with
        the command manager and the list of UAVs, further extended with the
        given positional and keyword arguments. When such a method does not
        exist, the handling of the command is forwarded to the
        ``handle_command_{command}()`` method instead, one by one for each UAV
        that is targeted. If this method does not exist either, the implementation
        will call the ``handle_multi_generic_command()`` method or the
        ``handle_generic_command()`` method instead, whose signatures should
        match the signature of ``send_command()`` (but of course the latter
        variant will receive a single UAV only). When none of these four
        methods exist, the default implementation simply throws a
        NotSupportedError_ exception.

        This function will return immediately, but the return value of the
        handler methods described above may be awaitables for some UAVs if the
        execution of the command takes a longer time. Awaitables should be
        handled by the caller appropriately. In particular, each awaitable
        must be awaited for by the caller.

        Parameters:
            uavs: the UAVs to address with this request.
            command: the command to send to the UAVs
            args (list): the list of positional arguments for the command
                (if the driver supports positional arguments)
            kwds (dict): the keyword arguments for the command (if the
                driver supports keyword arguments)

        Returns:
            dict[UAV,object]: dict mapping UAVs to the corresponding results.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        args = [] if args is None else args
        kwds = {} if kwds is None else kwds

        # Validate the command and the arguments. If the driver knows that
        # it won't be able to execute the command, it may return an error
        # message here
        error = self.validate_command(command, args, kwds)
        if error:
            return dict.fromkeys(uavs, error)

        # Figure out whether we will execute the commands for all the UAVs
        # at the same time, or one by one, depending on what is implemented
        # by the driver or not
        handlers = [
            (f"handle_multi_command_{command}", False, True),
            (f"handle_command_{command}", False, False),
            ("handle_generic_multi_command", True, True),
            ("handle_generic_command", True, False),
        ]

        for func_name, generic, multi in handlers:  # noqa: B007
            func = getattr(self, func_name, None)
            if func is not None:
                break
        else:
            raise NotSupportedError

        if multi:
            # Driver knows how to execute the command for multiple UAVs
            # at the same time
            if generic:
                return self._execute(func, uavs, command, args, kwds)
            else:
                return self._execute(func, uavs, *args, **kwds)
        else:
            # Driver can execute the command for a single UAV only so we need
            # to loop
            result = {}
            if generic:
                result = {
                    uav: self._execute(func, uav, command, args, kwds) for uav in uavs
                }
            else:
                result = {uav: self._execute(func, uav, *args, **kwds) for uav in uavs}
        return result

    def send_fly_to_target_signal(self, uavs: list[TUAV], target: GPSCoordinate):
        """Asks the driver to send a signal to the given UAVs that makes them
        fly to a given target coordinate. Every UAV passed as an argument is
        assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_fly_to_target_signal_single()`` instead.

        Parameters:
            uavs: the UAVs to address with this request
            target: the target to fly to; the altitude above
                home level may be set to `None` to indicate the current
                altitude of UAVs. Altitude above ground is not supported yet.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "fly to target signal",
            self._send_fly_to_target_signal_single,
            target=target,
        )

    def send_hover_signal(
        self,
        uavs: list[TUAV],
        *,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a signal to the given UAVs in order to
        request them to hover in place as soon as possible.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_hover_signal_single()`` and optionally
        ``_send_hover_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "position hold signal",
            self._send_hover_signal_single,
            getattr(self, "_send_hover_signal_broadcast", None),
            transport=transport,
        )

    def send_landing_signal(
        self, uavs: list[TUAV], transport: Optional[TransportOptions] = None
    ):
        """Asks the driver to send a landing signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_landing_signal_single()`` and optionally
        ``_send_landing_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "landing signal",
            self._send_landing_signal_single,
            getattr(self, "_send_landing_signal_broadcast", None),
            transport=transport,
        )

    def send_light_or_sound_emission_signal(
        self,
        uavs: list[TUAV],
        signals: list[str],
        duration: int,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a light or sound emission signal to the
        given UAVs, each of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_light_or_sound_emission_signal_single()``
        and optionally ``_send_light_or_sound_emission_signal_broadcast()``
        instead.

        Parameters:
            uavs: the UAVs to address with this request.
            signals: the list of signal types that the targeted UAVs should
                emit (e.g., 'sound', 'light')
            duration: the duration of the required signal in milliseconds
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "light or sound emission signal",
            self._send_light_or_sound_emission_signal_single,
            getattr(self, "_send_light_or_sound_emission_signal_broadcast", None),
            signals=signals,
            duration=duration,
            transport=transport,
        )

    def send_motor_start_stop_signal(
        self,
        uavs: list[TUAV],
        start: bool = False,
        force: bool = False,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a signal to start or stop the motors of the
        given UAVs, each of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_motor_start_stop_signal_single()`` and
        optionally ``_send_motor_start_stop_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            start: whether the motors should be started (`True`) or stopped
                (`False`)
            force: whether to force the execution of the command even if it is
                unsafe (e.g., stopping the motors while airborne)
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "motor start signal" if start else "motor stop signal",
            self._send_motor_start_stop_signal_single,
            getattr(self, "_send_motor_start_stop_signal_broadcast", None),
            start=start,
            force=force,
            transport=transport,
        )

    def send_reset_signal(
        self,
        uavs: list[TUAV],
        *,
        component: Optional[str] = None,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a reset signal to the given UAVs in order
        to restart some component of the UAV or the whole UAV itself.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_reset_signal_single()`` and optionally
        ``_send_reset_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            component: the component to reset. ``None`` or an empty string means
                to reset the entire UAV.
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "reset signal",
            self._send_reset_signal_single,
            getattr(self, "_send_reset_signal_broadcast", None),
            component=str(component or ""),
            transport=transport,
        )

    def send_return_to_home_signal(
        self, uavs: list[TUAV], transport: Optional[TransportOptions] = None
    ):
        """Asks the driver to send a return-to-home signal to the given
        UAVs, each of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_return_to_home_signal_single()`` and
        optionally ``_send_return_to_home_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request.
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "return to home signal",
            self._send_return_to_home_signal_single,
            getattr(self, "_send_return_to_home_signal_broadcast", None),
            transport=transport,
        )

    def send_shutdown_signal(
        self, uavs: list[TUAV], transport: Optional[TransportOptions] = None
    ):
        """Asks the driver to send a shutdown signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_shutdown_signal_single()`` and optionally
        ``_send_shutdown_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "shutdown signal",
            self._send_shutdown_signal_single,
            getattr(self, "_send_shutdown_signal_broadcast", None),
            transport=transport,
        )

    def send_takeoff_signal(
        self,
        uavs: list[TUAV],
        *,
        scheduled: bool = False,
        transport: Optional[TransportOptions] = None,
    ):
        """Asks the driver to send a takeoff signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_takeoff_signal_single()`` and optionally
        ``_send_takeoff_signal_broadcast()`` instead.

        Parameters:
            uavs: the UAVs to address with this request
            scheduled: whether the takeoff signal was scheduled earlier and is
                now issued autonomously by the server
            transport: transport options for sending the signal

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "takeoff signal",
            self._send_takeoff_signal_single,
            getattr(self, "_send_takeoff_signal_broadcast", None),
            scheduled=scheduled,
            transport=transport,
        )

    def set_parameter(self, uavs: list[TUAV], name: str, value: Any):
        """Asks the driver to set the value of a parameter on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_set_parameter_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "parameter upload",
            self._set_parameter_single,
            name=name,
            value=value,
        )

    def set_parameters(self, uavs: list[TUAV], parameters: dict[str, Any]):
        """Asks the driver to set the value of multiple parameters on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_set_parameters_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._dispatch_request(
            uavs,
            "bulk parameter upload",
            self._set_parameters_single,
            parameters=parameters,
        )

    def validate_command(self, command: str, args, kwds) -> Optional[str]:
        """Checks whether the driver could execute the command on the UAVs
        _in principle_, without knowing which UAVs the command will be sent to.

        This function can be used to bail out early from sending commands if
        we know already that the driver will not support the command.

        For instance, if the UAV commands do not support keyword arguments,
        you can check that here and bail out early if keyword arguments were
        supplied.

        The default implementation does nothing; no need to call it from
        subclasses.

        Parameters:
            command: the command to send to the UAVs
            args: the list of positional arguments for the command
                (if the driver supports positional arguments)
            kwds: the keyword arguments for the command (if the
                driver supports keyword arguments)

        Returns:
            an error message if the command cannot be executed, or `None` if
            the command can be executed
        """
        pass

    def _dispatch_request(
        self,
        uavs: list[TUAV],
        request_name: str,
        handler: Callable[..., TResult],
        broadcaster: Optional[Callable[..., TResult]] = None,
        **kwds,
    ) -> Union[TResult, Exception, dict[TUAV, Union[Exception, TResult]]]:
        """Common implementation for the body of several ``send_*_signal()``
        and similar methods in this class.

        The primary purpose of this function is to handle the most common case
        when a single operation has to be performed for a list of UAVs. The
        function assumes that there is a dedicated _handler_ function (either
        sync or async) that can perform the operation for a _single_ UAV, and
        optionally another, _broadcaster_ function that can perform the operation
        by broadcasting a message to all affected UAVs. The results from the
        individual handlers are then merged into a dictionary consisting of
        results (for UAVs with sync handlers) and awaitables (for UAVs with
        async handlers).

        Arguments:
            uavs: the list of UAVs to dispatch the request to
            request_name: name of the request (operation) for logging purposes,
                also to be used in error messages
            handler: the handler function that can perform the operation for a
                single UAV
            broadcaster: the broadcaster function that can perform the operation
                by broadcasting a message

        Returns:
            dictionary mapping UAVs to the corresponding result objects or
            awaitables, or a single result object if broadacsting was used
        """
        result = {}

        # Determine whether we need to broadcast this signal
        transport = kwds.get("transport")
        if transport:
            should_broadcast = TransportOptions.is_broadcast(transport)
        else:
            should_broadcast = False

        if should_broadcast:
            # We need to broadcast. Do we have a separate function for broadcasting?
            if broadcaster is not None:
                try:
                    outcome = broadcaster(**kwds)
                except NotImplementedError:
                    outcome = NotImplementedError(
                        f"Broadcasting {request_name} not implemented yet"
                    )
                except NotSupportedError as ex:
                    outcome = NotSupportedError(
                        str(ex) or f"Broadcasting {request_name} not supported"
                    )
                except RuntimeError as ex:
                    outcome = RuntimeError(
                        f"Error while broadcasting {request_name}: {str(ex)}"
                    )
                except Exception as ex:
                    log.exception(ex)
                    outcome = ex.__class__(
                        f"Unexpected error while broadcasting {request_name}: {ex!r}"
                    )
                return outcome

            # No separate function for broadcasting. Can we replace the call
            # with unicast calls?
            elif TransportOptions.should_ignore_ids(transport):
                # No, because we must ignore whatever IDs were submitted. In
                # this case we should return a "not supported" error
                return NotSupportedError(f"Broadcasting {request_name} not supported")

            # Fallthrough, continuing with unicast messages

        # We need to send this command one by one to all the UAVs
        for uav in uavs:
            try:
                outcome = handler(uav, **kwds)
            except NotImplementedError:
                outcome = NotImplementedError(f"{request_name} not implemented yet")
            except NotSupportedError as ex:
                outcome = NotSupportedError(str(ex) or f"{request_name} not supported")
            except RuntimeError as ex:
                outcome = RuntimeError(f"Error while sending {request_name}: {str(ex)}")
            except Exception as ex:
                log.exception(ex)
                outcome = ex.__class__(
                    f"Unexpected error while sending {request_name}: {ex!r}"
                )
            result[uav] = outcome

        return result

    def _calibrate_component_single(self, uav: TUAV, component: str):
        """Asks the driver to calibrate a component of a single UAV managed by
        this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            component: the component to calibrate.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        if hasattr(uav, "calibrate_component"):
            return uav.calibrate_component(component)  # type: ignore
        else:
            raise NotSupportedError

    def _enter_low_power_mode_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to request a single UAV to switch to low-power mode.

        May return an awaitable if sending the request takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        # Default is NotSupportedError because it is not that common for UAVs
        # to support low-power mode
        raise NotSupportedError

    def _get_log_list_single(
        self, uav: TUAV
    ) -> Union[list[FlightLogMetadata], Awaitable[list[FlightLogMetadata]]]:
        """Asks the driver to retrieve the list of flight logs from a single
        UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _get_parameter_single(self, uav: TUAV, name: str) -> Any:
        """Asks the driver to retrieve the value of a parameter with the given
        name from a single UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _resume_from_low_power_mode_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to resume normal operation for a a single UAV that is
        now in low-power mode.

        May return an awaitable if sending the request takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        # Default is NotSupportedError because it is not that common for UAVs
        # to support low-power mode
        raise NotSupportedError

    def _request_preflight_report_single(
        self, uav: TUAV
    ) -> Union[PreflightCheckInfo, Awaitable[PreflightCheckInfo]]:
        """Asks the driver to return a detailed report about the results of the
        preflight checks for a single UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _request_version_info_single(
        self, uav: TUAV
    ) -> Union[VersionInfo, Awaitable[VersionInfo]]:
        """Asks the driver to return a mapping from component names to the
        corresponding version numbers for a single UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_fly_to_target_signal_single(
        self, uav: TUAV, target: GPSCoordinate
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a "fly to target" signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            target: the target to fly to; the altitude above
                home level may be set to `None` to indicate the current
                altitude. Altitude above ground is not supported yet.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_hover_signal_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a position hold signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_landing_signal_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a landing signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_light_or_sound_emission_signal_single(
        self,
        uav: TUAV,
        signals: list[str],
        duration: int,
        *,
        transport: Optional[TransportOptions] = None,
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a light or sound emission signal to a
        single UAV managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all", with one exception. The specification says that signal
        names may be defined and extended by the user without modifying the
        formal protocol specification, and implementations should accept any
        signal name and simply not respond to signals that they do not know.
        Therefore, it is valid to return even if no visible light or audio
        signal was emitted by the UAV as long as no other errors happened.
        Raise an exception if the operation cannot be executed for any other
        reason; a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            signals: the list of signal types that the targeted UAV should emit
                (e.g., 'sound', 'light')
            duration: the duration of the required signal in milliseconds
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_motor_start_stop_signal_single(
        self,
        uav: TUAV,
        start: bool,
        force: bool = False,
        *,
        transport: Optional[TransportOptions] = None,
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a signal to start or stop the motors of the
        given UAVs, each of which are assumed to be managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            start: whether the motors should be started (`True`) or stopped
                (`False`)
            force: whether to force the execution of the command even if it is
                unsafe (e.g., stopping the motors while airborne)
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_reset_signal_single(
        self, uav: TUAV, component: str, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a reset signal to a single UAV managed by
        this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            component: the component to reset; an empty string means that the
                entire UAV should be reset.
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_return_to_home_signal_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a return-to-home signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_shutdown_signal_single(
        self, uav: TUAV, *, transport: Optional[TransportOptions] = None
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a shutdown signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_takeoff_signal_single(
        self,
        uav: TUAV,
        *,
        scheduled: bool = False,
        transport: Optional[TransportOptions] = None,
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to send a takeoff signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            transport: transport options for sending the signal

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _set_parameter_single(
        self, uav: TUAV, name: str, value: Any
    ) -> Union[None, Awaitable[None]]:
        """Asks the driver to set the value of a parameter with the given
        name for a single UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    async def _set_parameters_single(
        self, uav: TUAV, parameters: dict[str, Any]
    ) -> ProgressEvents[BulkParameterUploadResponse]:
        """Asks the driver to set the values of multiple parameters for a single
        UAV managed by this driver.

        May return an awaitable if preparing the result takes a longer time.

        Since we are dealing with multiple parameters here that may be changed
        independently, the function attempts to return even if some of the
        parameters have not been uploaded successfully. The result conveys
        more detailed information about whether the upload succeeded or not.

        The default implementation falls back to multiple calls to
        `_set_parameter_single()` in a sequential manner, sorted by keys in
        alphabetical order. Override this function in concrete driver
        implementations if you have a more efficient method for bulk parameter
        uploads.

        The default implementation always returns an awaitable as we have no
        way of knowing whether `_set_parameter_single()` is sync or async
        without calling it.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either

        Returns:
            a dictionary with a `success` key that contains whether all the
            parameters have been uploaded successfully, and an optional `failed`
            key that contains the list of parameter names where the upload
            failed.
        """
        failed: list[str] = []

        num_params = len(parameters)
        last_percentage = -1

        for index, name in enumerate(sorted(parameters.keys())):
            value = parameters[name]

            try:
                result = self._set_parameter_single(uav, name, value)
                if isawaitable(result):
                    await result
            except Exception:
                failed.append(name)

            percentage = int((index + 1) / num_params * 100)
            if percentage > last_percentage:
                yield Progress(percentage=percentage)
                last_percentage = percentage

        yield {"success": not failed, "failed": failed}


class PassiveUAV(UAVBase):
    pass


class PassiveUAVDriver(UAVDriver[PassiveUAV]):
    """Implementation of an UAVDriver_ for passive UAV objects that do not
    support responding to commands.
    """

    def __init__(self, uav_factory: Callable[..., PassiveUAV] = PassiveUAV):
        """Constructor.

        Parameters:
            uav_factory (callable): callable that creates a new UAV managed by
                this driver.
        """
        super(PassiveUAVDriver, self).__init__()
        self._uav_factory = uav_factory

    def _create_uav(self, id: str) -> PassiveUAV:
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id: the string identifier of the UAV to create

        Returns:
            an appropriate UAV object
        """
        return self._uav_factory(id, driver=self)

    def get_or_create_uav(self, id: str) -> PassiveUAV:
        """Retrieves the UAV with the given ID, or creates one if
        the driver has not seen a UAV with the given ID.

        Parameters:
            id: the identifier of the UAV to retrieve

        Returns:
            an appropriate UAV object
        """
        assert self.app is not None
        return self.app.object_registry.add_if_missing(id, factory=self._create_uav)

    def _dispatch_request(
        self, uavs: Iterable[UAV], request_name: str, handler, broadcaster=None, **kwds
    ) -> dict[UAV, Any]:
        error = RuntimeError("{0} not supported".format(request_name))
        return dict.fromkeys(uavs, error)


def is_uav(x: Any) -> bool:
    """Returns whether the given object is a UAV."""
    return isinstance(x, UAV)
