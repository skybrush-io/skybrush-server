"""Model classes related to a single UAV."""

from __future__ import absolute_import

from abc import ABCMeta, abstractproperty
from typing import Any, Optional

from flockwave.gps.vectors import GPSCoordinate, VelocityNED
from flockwave.server.errors import NotSupportedError
from flockwave.server.logger import log as base_log
from flockwave.spec.schema import get_complex_object_schema

from .devices import ObjectNode
from .metamagic import ModelMeta
from .mixins import TimestampLike, TimestampMixin
from .object import ModelObject, register
from .utils import scaled_by

__all__ = (
    "BatteryInfo",
    "is_uav",
    "PassiveUAVDriver",
    "UAV",
    "UAVBase",
    "UAVDriver",
    "UAVStatusInfo",
)

log = base_log.getChild("uav")


class BatteryInfo:
    """Class representing the battery information of a single UAV."""

    def __init__(self):
        self._voltage = None
        self._percentage = None

    @property
    def percentage(self) -> Optional[int]:
        return self._percentage

    @percentage.setter
    def percentage(self, value: Optional[int]) -> None:
        self._percentage = int(value) if value is not None else None

    @property
    def voltage(self) -> Optional[float]:
        return self._voltage

    @voltage.setter
    def voltage(self, value: Optional[float]) -> None:
        self._voltage = float(value) if value is not None else None

    @property
    def json(self):
        if self.voltage is None:
            return [0.0]
        elif self.percentage is None:
            return [int(round(self.voltage * 10))]
        else:
            return [int(round(self.voltage * 10)), self.percentage]

    @json.setter
    def json(self, value):
        if len(value) == 0:
            self._voltage = self._percentage = None
        else:
            self._voltage = value[0] / 10
            self._percentage = None if len(value) < 2 else int(value[1])

    def update_from(self, other):
        self._voltage = other._voltage
        self._percentage = other._percentage


class UAVStatusInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single
    UAV.
    """

    class __meta__:
        schema = get_complex_object_schema("uavStatusInfo")
        mappers = {"heading": scaled_by(10)}

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
        self.heading = 0.0
        self.id = id
        self.position = GPSCoordinate()
        self.velocity = VelocityNED()
        self.battery = BatteryInfo()


@register("uav")
class UAV(ModelObject, metaclass=ABCMeta):
    """Abstract object that defines the interface of objects representing
    UAVs.
    """

    @abstractproperty
    def driver(self):
        """Returns the UAVDriver_ object that is responsible for handling
        communication with this UAV.
        """
        raise NotImplementedError

    @abstractproperty
    def id(self):
        """A unique identifier for the UAV, assigned at construction
        time.
        """
        raise NotImplementedError

    @abstractproperty
    def status(self):
        """Returns an UAVStatusInfo_ object representing the status of the
        UAV.
        """
        raise NotImplementedError


class UAVBase(UAV):
    """Base object for UAV implementations. Provides a default implementation
    of the methods required by the UAV_ interface.
    """

    def __init__(self, id, driver):
        """Constructor.

        Parameters:
            id (str): the unique identifier of the UAV
            driver (UAVDriver): the driver that is responsible for handling
                communication with this UAV.
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
    def driver(self):
        """Returns the UAVDriver_ object that is responsible for handling
        communication with this UAV.
        """
        return self._driver

    @property
    def id(self):
        """A unique identifier for the UAV, assigned at construction
        time.
        """
        return self._id

    @property
    def status(self):
        """Returns an UAVStatusInfo_ object representing the status of the
        UAV.

        This property should be manipulated via the ``update_status()``
        method.
        """
        return self._status

    def _initialize_device_tree_node(self, node):
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

    def clear_errors(self):
        """Clears the error codes of the UAV."""
        return self.update_status(errors=())

    def update_status(
        self,
        position=None,
        velocity=None,
        heading=None,
        algorithm=None,
        battery=None,
        errors=None,
    ):
        """Updates the status information of the UAV.

        Parameters with values equal to ``None`` are ignored.

        Parameters:
            position (Optional[GPSCoordinate]): the position of the UAV.
                It will be cloned to ensure that modifying this position
                object from the caller will not affect the UAV itself.
            velocity (Optional[VelocityNED]): the velocity of the UAV.
                It will be cloned to ensure that modifying this velocity
                object from the caller will not affect the UAV itself.
            heading (Optional[float]): the heading of the UAV, in degrees.
            algorithm (Optional[str]): the algorithm that the UAV is
                currently executing
            battery (Optional[BatteryInfo]): information about the status
                of the battery on the UAV. It will be cloned to ensure that
                modifying this object from the caller will not affect the
                UAV itself.
            errors (Optional[Union[int,Iterable[int]]]): the error code or
                error codes of the UAV; use an empty list or tuple if the
                UAV has no errors
        """
        if position is not None:
            self._status.position.update_from(position, precision=7)
        if heading is not None:
            # Heading is rounded to 2 digits; it is unlikely that more
            # precision is needed and it saves space in the JSON
            # representation
            self._status.heading = round(heading % 360, 2)
        if velocity is not None:
            self._status.velocity.update_from(velocity, precision=2)
        if algorithm is not None:
            self._status.algorithm = algorithm
        if battery is not None:
            self._status.battery.update_from(battery)
        if errors is not None:
            if isinstance(errors, int):
                errors = [errors] if errors > 0 else []
            else:
                errors = sorted(code for code in errors if code > 0)
            self._status.errors = errors
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
        app (FlockwaveServer): the Flockwave server application that hosts
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

    def send_command(self, uavs, command, args=None, kwds=None):
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
            uavs (List[UAV]): the UAVs to address with this request.
            command (str): the command to send to the UAVs
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

    def send_fly_to_target_signal(self, uavs, target):
        """Asks the driver to send a signal to the given UAVs that makes them
        fly to a given target coordinate. Every UAV passed as an argument is
        assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_fly_to_target_signal_single()`` instead.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.
        """
        return self._send_signal(
            uavs,
            "fly to target signal",
            self._send_fly_to_target_signal_single,
            target=target,
        )

    def send_landing_signal(self, uavs):
        """Asks the driver to send a landing signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_landing_signal_single()`` instead.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        return self._send_signal(
            uavs, "landing signal", self._send_landing_signal_single
        )

    def send_reset_signal(self, uavs, *, component: Optional[str] = None):
        """Asks the driver to send a reset signal to the given UAVs in order
        to restart some component of the UAV or the whole UAV itself.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_reset_signal_single()`` instead.

        Parameters:
            component: the component to reset. ``None`` or an empty string means
                to reset the entire UAV.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.
        """
        return self._send_signal(
            uavs,
            "reset signal",
            self._send_reset_signal_single,
            component=str(component or ""),
        )

    def send_return_to_home_signal(self, uavs):
        """Asks the driver to send a return-to-home signal to the given
        UAVs, each of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_return_to_home_signal_single()`` instead.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.
        """
        return self._send_signal(
            uavs, "return to home signal", self._send_return_to_home_signal_single
        )

    def send_shutdown_signal(self, uavs):
        """Asks the driver to send a shutdown signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_shutdown_signal_single()`` instead.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.
        """
        return self._send_signal(
            uavs, "shutdown signal", self._send_shutdown_signal_single
        )

    def send_takeoff_signal(self, uavs):
        """Asks the driver to send a takeoff signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Typically, you don't need to override this method when implementing
        a driver; override ``_send_takeoff_signal_single()`` instead.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.
        """
        return self._send_signal(
            uavs, "takeoff signal", self._send_takeoff_signal_single
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

    def _send_signal(self, uavs, signal_name, handler, *args, **kwds):
        """Common implementation for the body of several ``send_*_signal()``
        methods in this class.
        """
        result = {}
        for uav in uavs:
            try:
                outcome = handler(uav, *args, **kwds)
            except NotImplementedError:
                outcome = f"{signal_name} not implemented yet"
            except NotSupportedError:
                outcome = f"{signal_name} not supported"
            except Exception as ex:
                log.exception(ex)
                outcome = f"Unexpected error while sending {signal_name}: {repr(ex)}"
            result[uav] = outcome
        return result

    def _send_fly_to_target_signal_single(self, uav, target):
        """Asks the driver to send a "fly to target" signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav (UAV): the UAV to address with this request.
            target (object): the target to fly to

        Returns:
            bool: whether the signal was *sent* successfully

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_landing_signal_single(self, uav):
        """Asks the driver to send a landing signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav (UAV): the UAV to address with this request.

        Returns:
            bool: whether the signal was *sent* successfully

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_reset_signal_single(self, uav: UAV, *, component: str):
        """Asks the driver to send a reset signal to a single UAV managed by
        this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav: the UAV to address with this request.
            component: the component to reset; an empty string means that the
                entire UAV should be reset.

        Returns:
            bool: whether the signal was *sent* successfully

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_return_to_home_signal_single(self, uav):
        """Asks the driver to send a return-to-home signal to a single UAV
        managed by this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav (UAV): the UAV to address with this request.

        Returns:
            bool: whether the signal was *sent* successfully

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_shutdown_signal_single(self, uav):
        """Asks the driver to send a shutdown signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav (UAV): the UAV to address with this request.

        Returns:
            bool: whether the signal was *sent* successfully

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver yet, but there are plans to implement it
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError

    def _send_takeoff_signal_single(self, uav):
        """Asks the driver to send a takeoff signal to a single UAV managed
        by this driver.

        May return an awaitable if sending the signal takes a longer time.

        Parameters:
            uav (UAV): the UAV to address with this request.

        Returns:
            bool: whether the signal was *sent* successfully

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

    def __init__(self, uav_factory=PassiveUAV):
        """Constructor.

        Parameters:
            uav_factory (callable): callable that creates a new UAV managed by
                this driver.
        """
        super(PassiveUAVDriver, self).__init__()
        self._uav_factory = uav_factory

    def _create_uav(self, id):
        """Creates a new UAV that is to be managed by this driver.

        Parameters:
            id (str): the string identifier of the UAV to create

        Returns:
            UAVBase: an appropriate UAV object
        """
        return self._uav_factory(id, driver=self)

    def get_or_create_uav(self, id):
        """Retrieves the UAV with the given ID, or creates one if
        the driver has not seen a UAV with the given ID.

        Parameters:
            id (str): the identifier of the UAV to retrieve

        Returns:
            UAVBase: an appropriate UAV object
        """
        object_registry = self.app.object_registry
        if not object_registry.contains(id):
            uav = self._create_uav(id)
            object_registry.add(uav)
        return object_registry.find_by_id(id)

    def _send_signal(self, uavs, signal_name, handler):
        message = "{0} not supported".format(signal_name)

        result = {}
        for uav in uavs:
            result[uav] = message

        return result


def is_uav(x: Any) -> bool:
    """Returns whether the given object is a UAV."""
    return isinstance(x, UAV)
