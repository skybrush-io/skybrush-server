"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from blinker import Signal
from importlib import import_module
from logging import Logger
from os import devnull
from typing import Callable, Optional, Union


def get_mavlink_parser_factory(dialect: Union[str, Callable]):
    """Constructs a function that can be called with a file-like object and that
    constructs a new MAVLink parser.

    Parameters:
        dialect: the name of the MAVLink dialect that will be used by the
            parser. When it is a callable, it is returned intact.
    """
    if callable(dialect):
        return dialect

    module = import_module(f"pymavlink.dialects.v20.{dialect}")
    return module.MAVLink


class CommunicationManager:
    """Abstract communication manager base class and interface specification.

    Attributes:
        identifier (str): unique identifier of the communication manager;
            see the constructor documentation for its purpose
        on_packet (Signal): signal that is emitted when the communication
            manager receives a new MAVLink packet from an UAV. The signal is
            called with the parsed MAVLink packet class instance as its only
            argument.
    """

    on_packet = Signal()

    def __init__(
        self, ext, identifier: str, dialect: Union[str, Callable] = "ardupilotmega"
    ):
        """Constructor.

        Parameters:
            ext (MAVLinkDronesExtension): the extension that owns this
                manager
            identifier: unique identifier of this communication mananger. The
                purpose of this identifier is that the ``(identifier, system_id)``
                pair of a UAV must be unique (in other words, each UAV must
                have a unique system ID *within* each communication link that
                we handle)
            dialect (Union[str, callable]): the MAVLink dialect to use or
                a callable that can be called with no arguments to construct
                a MAVLink instance. When it is a dialect name, we will attempt
                to import `MAVLink` from `pymavlink.dialects.v20.{dialect}`
        """
        self.ext = ext
        self.identifier = identifier

        parser_factory = get_mavlink_parser_factory(dialect)
        self._parser = parser_factory(file=open(devnull, "wb"))

    @property
    def log(self) -> Optional[Logger]:
        """Returns the logger of the extension that owns this manager.

        Returns:
            the logger of the extension that owns this manager, or ``None`` if
            the manager is not associated to an extension yet.
        """
        return self.ext.log if self.ext else None
