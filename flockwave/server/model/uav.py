"""Model classes related to a single UAV."""

from __future__ import absolute_import

from abc import ABCMeta, abstractproperty
from flockwave.gps.vectors import GPSCoordinate, VelocityNED
from flockwave.server.errors import CommandInvocationError, NotSupportedError
from flockwave.spec.schema import get_complex_object_schema
from future.utils import with_metaclass, raise_with_traceback

from .devices import UAVNode
from .metamagic import ModelMeta
from .mixins import TimestampMixin

__all__ = ("UAVStatusInfo", "UAVDriver", "UAV", "UAVBase")


class UAVStatusInfo(with_metaclass(ModelMeta, TimestampMixin)):
    """Class representing the status information available about a single
    UAV.
    """

    class __meta__:
        schema = get_complex_object_schema("uavStatusInfo")

    def __init__(self, id=None, timestamp=None):
        """Constructor.

        Parameters:
            id (Optional[str]): ID of the UAV
            timestamp (Optional[datetime]): time when the status information
                was received. ``None`` means to use the current date and
                time.
        """
        TimestampMixin.__init__(self, timestamp)
        self.heading = 0.0
        self.id = id
        self.position = GPSCoordinate()
        self.velocity = VelocityNED()


class UAV(with_metaclass(ABCMeta, object)):
    """Abstract object that defines the interface of objects representing
    UAVs.
    """

    @abstractproperty
    def device_tree_node(self):
        """Returns the UAVNode_ object that represents the root of the
        part of the device tree that corresponds to the UAV.
        """
        raise NotImplementedError

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

        Typically, you do not construct UAV objects on your own but use the
        ``create_uav()`` method of an appropriate UAVDriver_ object. This
        method will also ensure that the UAV object is linked properly to
        its driver.

        Parameters:
            id (str): the unique identifier of the UAV
            driver (UAVDriver): the driver that is responsible for handling
                communication with this UAV.
        """
        self._device_tree_node = UAVNode()
        self._driver = driver
        self._id = id
        self._status = UAVStatusInfo(id=id)
        self._initialize_device_tree_node(self._device_tree_node)

    @property
    def device_tree_node(self):
        """Returns the UAVNode object that represents the root of the
        device tree corresponding to the UAV.

        Returns:
            UAVNode: the node in the device tree where the subtree of the
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
            node (UAVNode): the tree node whose subtree this call should
                initialize
        """
        pass

    def update_status(self, position=None, velocity=None, heading=None,
                      algorithm=None):
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
        """
        if position is not None:
            self._status.position.update_from(position)
        if heading is not None:
            self._status.heading = heading % 360
        if velocity is not None:
            self._status.velocity.update_from(velocity)
        if algorithm is not None:
            self._status.algorithm = algorithm
        self._status.update_timestamp()


class UAVDriver(with_metaclass(ABCMeta, object)):
    """Interface specification for UAV drivers that are responsible for
    handling communication with a given group of UAVs via a common
    communication channel (e.g., an XBee radio).

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

    def __init__(self):
        """Constructor."""
        self.app = None

    def send_command(self, uavs, command, args=None, kwds=None):
        """Asks the driver to send a direct command to the given UAVs, each
        of which are assumed to be managed by this driver.

        The default implementation of this method passes on each command
        to the ``handle_command_{command}()`` method where ``{command}`` is
        replaced by the command argument. The method will be called with
        the list of UAVs, extended with the given positional and keyword
        arguments. When such a method does not exist, the handling
        of the command is forwarded to the ``handle_generic_command()``
        method instead, whose signature should match the signature of
        ``send_command()``. When neither of these two methods exist, the
        default implementation simply throws a NotSupportedError_ exception.

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
        func = getattr(self, "handle_command_{0}".format(command), None)
        if func is None:
            func = getattr(self, "handle_generic_command", None)
            if func is None:
                raise NotSupportedError
            else:
                try:
                    return func(uavs, command, args, kwds)
                except Exception as ex:
                    raise_with_traceback(CommandInvocationError(cause=ex))
        else:
            if args is None:
                args = []
            if kwds is None:
                kwds = {}
            try:
                return func(uavs, *args, **kwds)
            except Exception as ex:
                raise_with_traceback(CommandInvocationError(cause=ex))

    def send_landing_signal(self, uavs):
        """Asks the driver to send a landing signal to the given UAVs, each
        of which are assumed to be managed by this driver.

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
        raise NotImplementedError

    def send_takeoff_signal(self, uavs):
        """Asks the driver to send a takeoff signal to the given UAVs, each
        of which are assumed to be managed by this driver.

        Parameters:
            uavs (List[UAV]): the UAVs to address with this request.

        Returns:
            Dict[UAV,object]: dict mapping UAVs to the corresponding results.

        Raises:
            NotImplementedError: if the operation is not supported by the
                driver at all
            NotSupportedError: if the operation is not supported by the
                driver and will not be supported in the future either
        """
        raise NotImplementedError
