"""Model classes related to a single UAV."""

from __future__ import absolute_import

from abc import ABCMeta, abstractproperty
from flockwave.gps.vectors import GPSCoordinate
from flockwave.spec.schema import get_complex_object_schema
from six import with_metaclass
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
        self.id = id
        self.position = GPSCoordinate()


class UAV(with_metaclass(ABCMeta, object)):
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

        Typically, you do not construct UAV objects on your own but use the
        ``create_uav()`` method of an appropriate UAVDriver_ object. This
        method will also ensure that the UAV object is linked properly to
        its driver.

        Parameters:
            id (str): the unique identifier of the UAV
            driver (UAVDriver): the driver that is responsible for handling
                communication with this UAV.
        """
        self._driver = driver
        self._id = id
        self._status = UAVStatusInfo(id=id)

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

    def update_status(self, position=None):
        """Updates the status information of the UAV.

        Parameters:
            position (GPSCoordinate): the position of the UAV. It will be
                cloned to ensure that modifying this position object from
                the caller will not affect the UAV itself.
        """
        if position is not None:
            self._status.position.update_from(position)
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
    the operation succeeded; anything else means failure. Failures should
    be denoted by strings explaining the reason of the failure.

    It is the responsibility of the implementor of these methods to ensure
    that all the UAVs that appeared in the input UAV list are also mentioned
    in the dictionary that is returned from the method.
    """

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
