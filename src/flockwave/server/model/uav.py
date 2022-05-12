"""Model classes related to a single UAV."""

from __future__ import annotations

from abc import ABCMeta, abstractproperty
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

from flockwave.gps.vectors import GPSCoordinate, PositionXYZ, VelocityNED, VelocityXYZ
from flockwave.server.errors import NotSupportedError
from flockwave.server.model.transport import TransportOptions
from flockwave.server.logger import log as base_log
from flockwave.spec.schema import get_complex_object_schema

from .battery import BatteryInfo
from .devices import ObjectNode
from .gps import GPSFix, GPSFixLike
from .metamagic import ModelMeta
from .mixins import TimestampLike, TimestampMixin
from .object import ModelObject, register
from .preflight import PreflightCheckInfo
from .utils import as_base64, scaled_by

__all__ = (
    "is_uav",
    "PassiveUAVDriver",
    "UAV",
    "UAVBase",
    "UAVDriver",
    "UAVStatusInfo",
)

log = base_log.getChild("uav")


#: Type alias for version information objects returned from UAVDriver, mapping
#: component names to version numbers
VersionInfo = Dict[str, str]


class UAVStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single
    UAV.
    """

    class __meta__:
        schema = get_complex_object_schema("uavStatusInfo")
        mappers = {"heading": scaled_by(10), "debug": as_base64}

    debug: bytes
    errors: List[int]
    gps: GPSFix
    heading: float
    id: str
    light: int
    mode: str
    position: GPSCoordinate
    positionXYZ: Optional[PositionXYZ]
    velocity: VelocityNED
    velocityXYZ: Optional[VelocityXYZ]
    battery: BatteryInfo

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
        self.id = id  # type: ignore
        self.light = 0  # black
        self.mode = ""
        self.position = GPSCoordinate()
        self.velocity = VelocityNED()
        self.positionXYZ = None
        self.velocityXYZ = None
        self.battery = BatteryInfo()

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
class UAV(ModelObject, metaclass=ABCMeta):
    """Abstract object that defines the interface of objects representing
    UAVs.
    """

    @abstractproperty
    def driver(self) -> "UAVDriver":
        """Returns the UAVDriver_ object that is responsible for handling
        communication with this UAV.
        """
        raise NotImplementedError

    @abstractproperty
    def id(self) -> str:
        """A unique identifier for the UAV, assigned at construction
        time.
        """
        raise NotImplementedError

    @abstractproperty
    def status(self) -> UAVStatusInfo:
        """Returns an UAVStatusInfo_ object representing the status of the
        UAV.
        """
        raise NotImplementedError


class UAVBase(UAV):
    """Base object for UAV implementations. Provides a default implementation
    of the methods required by the UAV_ interface.
    """

    def __init__(self, id: str, driver: "UAVDriver"):
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
    def device_tree_node(self):
        """Returns the ObjectNode object that represents the root of the
        device tree corresponding to the UAV.

        Returns:
            ObjectNode: the node in the device tree where the subtree of the
                devices and channels of the UAV is rooted
        """
        return self._device_tree_node

    @property
    def driver(self) -> "UAVDriver":
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

    def _initialize_device_tree_node(self, node) -> None:
        """Initializes the device tree node of the UAV when it is
        constructed.

        This method will be called from the constructor. Subclasses may
        override this method to provide a set of default devices for the
        UAV.

        Parameters:
            node (ObjectNode): the tree node whose subtree this call should
                initialize
        """
        pass

    def clear_errors(self) -> None:
        """Clears the error codes of the UAV."""
        return self.update_status(errors=())

    def convert_agl_to_amsl(
        self, altitude: float, *, current_agl: Optional[float] = None
    ) -> float:
        """Converts an altitude given as altitude above ground level to altitude
        above mean sea level.

        This function requires the drone to know its current AGL and AMSL so it
        can calculate an offset between them. Alternatively, if the `current_agl`
        argument is not `None`, the given value is used as the current AGL.

        Returns:
            the given AGL altitude converted to AMSL

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
                "Cannot convert AGL to AMSL, current position not known yet"
            )

        if pos.amsl is None:
            raise RuntimeError("Cannot convert AGL to AMSL, current AMSL not known yet")

        agl = current_agl if current_agl is not None else pos.agl
        if agl is None:
            raise RuntimeError("Cannot convert AGL to AMSL, current AGL not known yet")

        return altitude - agl + pos.amsl

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

    def ensure_errors(self, codes: Dict[int, bool]) -> None:
        """Updates multiple error codes with a single function call.

        Parameters:
            codes: dictionary mapping error codes to a boolean specifying
                whether the error code should be present or absent
        """
        if self._status.errors or any(present for present in codes.values()):
            for code, present in codes.items():
                self.ensure_error(code, present)

    def update_status(
        self,
        *,
        position: Optional[GPSCoordinate] = None,
        position_xyz: Optional[PositionXYZ] = None,
        velocity: Optional[VelocityNED] = None,
        velocity_xyz: Optional[VelocityXYZ] = None,
        heading: Optional[float] = None,
        mode: Optional[str] = None,
        gps: Optional[GPSFixLike] = None,
        battery: Optional[BatteryInfo] = None,
        light: Optional[int] = None,
        errors: Optional[Union[int, Iterable[int]]] = None,
        debug: Optional[bytes] = None,
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
        if gps is not None:
            self._status.gps.update_from(gps)
        if debug is not None:
            self._status.debug = debug
        self._status.update_timestamp()


class UAVDriver(metaclass=ABCMeta):
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

    Attributes:
        app (SkybrushServer): the Skybrush server application that hosts
            the driver
    """

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
        self.app = None

    def enter_low_power_mode(
        self, uavs: List[UAV], transport: Optional[TransportOptions] = None
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
        return self._send_signal(
            uavs,
            "low-power mode request",
            self._enter_low_power_mode_single,
            getattr(self, "_enter_low_power_mode_broadcast", None),
            transport=transport,
        )

    def get_parameter(self, uavs: List[UAV], name: str) -> Dict[UAV, Any]:
        """Asks the driver to retrieve the current value of a parameter from
        the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_get_parameter_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._send_signal(
            uavs, "parameter retrieval", self._get_parameter_single, name=name
        )

    def request_preflight_report(
        self, uavs: List[UAV]
    ) -> Dict[UAV, PreflightCheckInfo]:
        """Asks the driver to request a detailed report about the status of
        preflight checks on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_request_preflight_report_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._send_signal(
            uavs,
            "preflight report request",
            self._request_preflight_report_single,
        )

    def request_version_info(self, uavs: List[UAV]) -> Dict[UAV, VersionInfo]:
        """Asks the driver to request detailed version information from the
        given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_request_version_info_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._send_signal(
            uavs, "version info request", self._request_version_info_single
        )

    def resume_from_low_power_mode(
        self, uavs: List[UAV], transport: Optional[TransportOptions] = None
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
        return self._send_signal(
            uavs,
            "wakeup request",
            self._resume_from_low_power_mode_single,
            getattr(self, "_resume_from_low_power_mode_broadcast", None),
            transport=transport,
        )

    def send_command(self, uavs: List[UAV], command: str, args=None, kwds=None):
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
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.

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
            return {uav: error for uav in uavs}

        # Figure out whether we will execute the commands for all the UAVs
        # at the same time, or one by one, depending on what is implemented
        # by the driver or not
        handlers = [
            (f"handle_multi_command_{command}", False, True),
            (f"handle_command_{command}", False, False),
            ("handle_generic_multi_command", True, True),
            ("handle_generic_command", True, False),
        ]

        for func_name, generic, multi in handlers:
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

    def send_fly_to_target_signal(self, uavs: List[UAV], target):
        """Asks the driver to send a signal to the given UAVs that makes them
        fly to a given target coordinate. Every UAV passed as an argument is
        assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_fly_to_target_signal_single()`` instead.

        Parameters:
            uavs: the UAVs to address with this request

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._send_signal(
            uavs,
            "fly to target signal",
            self._send_fly_to_target_signal_single,
            target=target,
        )

    def send_hover_signal(
        self,
        uavs: List[UAV],
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
        return self._send_signal(
            uavs,
            "position hold signal",
            self._send_hover_signal_single,
            getattr(self, "_send_hover_signal_broadcast", None),
            transport=transport,
        )

    def send_landing_signal(
        self, uavs: List[UAV], transport: Optional[TransportOptions] = None
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
        return self._send_signal(
            uavs,
            "landing signal",
            self._send_landing_signal_single,
            getattr(self, "_send_landing_signal_broadcast", None),
            transport=transport,
        )

    def send_light_or_sound_emission_signal(
        self,
        uavs: List[UAV],
        signals: List[str],
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
        return self._send_signal(
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
        uavs: List[UAV],
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
        return self._send_signal(
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
        uavs: List[UAV],
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
        return self._send_signal(
            uavs,
            "reset signal",
            self._send_reset_signal_single,
            getattr(self, "_send_reset_signal_broadcast", None),
            component=str(component or ""),
            transport=transport,
        )

    def send_return_to_home_signal(
        self, uavs: List[UAV], transport: Optional[TransportOptions] = None
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
        return self._send_signal(
            uavs,
            "return to home signal",
            self._send_return_to_home_signal_single,
            getattr(self, "_send_return_to_home_signal_broadcast", None),
            transport=transport,
        )

    def send_shutdown_signal(
        self, uavs: List[UAV], transport: Optional[TransportOptions] = None
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
        return self._send_signal(
            uavs,
            "shutdown signal",
            self._send_shutdown_signal_single,
            getattr(self, "_send_shutdown_signal_broadcast", None),
            transport=transport,
        )

    def send_takeoff_signal(
        self,
        uavs: List[UAV],
        *,
        scheduled: bool = False,
        transport: Optional[TransportOptions] = None,
    ) -> Dict[UAV, object]:
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
        return self._send_signal(
            uavs,
            "takeoff signal",
            self._send_takeoff_signal_single,
            getattr(self, "_send_takeoff_signal_broadcast", None),
            scheduled=scheduled,
            transport=transport,
        )

    def set_parameter(self, uavs: List[UAV], name: str, value: Any) -> Dict[UAV, Any]:
        """Asks the driver to set the value of a parameter on the given UAVs.

        Typically, you don't need to override this method when implementing
        a driver; override ``_set_parameter_single()`` instead.

        Returns:
            dict mapping UAVs to the corresponding results (which may also be
            errors or awaitables; it is the responsibility of the caller to
            evaluate errors and wait for awaitables)
        """
        return self._send_signal(
            uavs,
            "parameter upload",
            self._set_parameter_single,
            name=name,
            value=value,
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

    def _send_signal(
        self, uavs: List[UAV], signal_name: str, handler, broadcaster=None, **kwds
    ) -> Union[Any, Dict[UAV, Any]]:
        """Common implementation for the body of several ``send_*_signal()``
        methods in this class.
        """
        result = {}

        # Determine whether we need to broadcast this signal
        is_broadcast = False
        if broadcaster and "transport" in kwds:
            transport = kwds.get("transport")
            if isinstance(transport, TransportOptions):
                is_broadcast = bool(getattr(transport, "broadcast", False))

        if is_broadcast:
            # We need to broadcast and we know that we have a separate function
            # for broadcasting
            try:
                outcome = broadcaster(**kwds)
            except NotImplementedError:
                outcome = NotImplementedError(
                    f"Broadcasting {signal_name} not implemented yet"
                )
            except NotSupportedError as ex:
                outcome = NotSupportedError(
                    str(ex) or f"Broadcasting {signal_name} not supported"
                )
            except RuntimeError as ex:
                outcome = RuntimeError(
                    f"Error while broadcasting {signal_name}: {str(ex)}"
                )
            except Exception as ex:
                log.exception(ex)
                outcome = ex.__class__(
                    f"Unexpected error while broadcasting {signal_name}: {ex!r}"
                )
            return outcome

        else:
            # We need to send this command one by one to all the UAVs
            for uav in uavs:
                try:
                    outcome = handler(uav, **kwds)
                except NotImplementedError:
                    outcome = NotImplementedError(f"{signal_name} not implemented yet")
                except NotSupportedError as ex:
                    outcome = NotSupportedError(
                        str(ex) or f"{signal_name} not supported"
                    )
                except RuntimeError as ex:
                    outcome = RuntimeError(
                        f"Error while sending {signal_name}: {str(ex)}"
                    )
                except Exception as ex:
                    log.exception(ex)
                    outcome = ex.__class__(
                        f"Unexpected error while sending {signal_name}: {ex!r}"
                    )
                result[uav] = outcome

        return result

    def _enter_low_power_mode_single(
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ) -> None:
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

    def _get_parameter_single(self, uav: UAV, name: str) -> Any:
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
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ) -> None:
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

    def _request_preflight_report_single(self, uav: UAV) -> PreflightCheckInfo:
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

    def _request_version_info_single(self, uav: UAV) -> VersionInfo:
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

    def _send_fly_to_target_signal_single(self, uav: UAV, target) -> None:
        """Asks the driver to send a "fly to target" signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        The function follows the "samurai principle", i.e. "return victorious,
        or not at all". It means that if it returns, the operation succeeded.
        Raise an exception if the operation cannot be executed for any reason;
        a RuntimeError is typically sufficient.

        Parameters:
            uav: the UAV to address with this request.
            target (GPSCoordinate): the target to fly to; the altitude above
                ground level may be set to `None` to indicate the current
                altitude

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_hover_signal_single(
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ) -> None:
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
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ) -> None:
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
        uav: UAV,
        signals: List[str],
        duration: int,
        *,
        transport: Optional[TransportOptions] = None,
    ) -> None:
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
        uav: UAV,
        start: bool,
        force: bool = False,
        *,
        transport: Optional[TransportOptions] = None,
    ) -> None:
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
        self, uav: UAV, *, component: str, transport: Optional[TransportOptions] = None
    ) -> None:
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
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ):
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
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ):
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
        self, uav: UAV, *, transport: Optional[TransportOptions] = None
    ):
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

    def _set_parameter_single(self, uav: UAV, name: str, value: Any) -> None:
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


class PassiveUAV(UAVBase):
    pass


class PassiveUAVDriver(UAVDriver):
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

    def _send_signal(
        self, uavs: Iterable[UAV], signal_name: str, handler, broadcaster=None, **kwds
    ) -> Dict[UAV, Any]:
        error = RuntimeError("{0} not supported".format(signal_name))
        return {uav: error for uav in uavs}


def is_uav(x: Any) -> bool:
    """Returns whether the given object is a UAV."""
    return isinstance(x, UAV)
