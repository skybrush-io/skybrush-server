"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from importlib import import_module
from typing import Any, Callable, List, Union, Tuple

from flockwave.channels import MessageChannel
from flockwave.connections import Connection
from flockwave.logger import Logger

from flockwave.server.comm import CommunicationManager

from .enums import MAVComponent
from .types import MAVLinkMessage, MAVLinkNetworkSpecification


__all__ = ("create_communication_manager",)


def get_mavlink_factory(dialect: Union[str, Callable]):
    """Constructs a function that can be called with no arguments and that will
    construct a new MAVLink parser.

    Parameters:
        dialect: the name of the MAVLink dialect that will be used by the
            parser. When it is a callable, it is returned intact.
    """
    if callable(dialect):
        return dialect

    module = import_module(f"pymavlink.dialects.v20.{dialect}")

    def factory():
        # Use robust parsing so we don't freak out if we see some noise on the
        # line
        # TODO(ntamas): initialize system ID properly from config
        link = module.MAVLink(
            None, srcSystem=255, srcComponent=MAVComponent.MISSIONPLANNER
        )
        link.robust_parsing = True
        return link

    return factory


def create_communication_manager() -> CommunicationManager[Any, Any]:
    """Creates a communication manager instance for the extension."""
    return CommunicationManager(channel_factory=create_mavlink_message_channel)


def create_mavlink_message(link, type: str, *args, **kwds) -> MAVLinkMessage:
    """Creates a MAVLink message from the methods of a MAVLink object received
    from the low-level `pymavlink` library.

    Parameters:
        link: the low-level MAVLink object
        type: the type of the message to construct. It will be lower-cased
            automatically and it will be used to look up a function named
            `<type>_encode` on the low-level MAVLink object

    Additional positional and keyword arguments are forwarded to the appropriate
    function of the low-level MAVLink object.

    Returns:
        the MAVLink message as returned from the low-level MAVLink object
    """
    try:
        func = getattr(link, f"{type.lower()}_encode")
    except AttributeError:
        raise ValueError(f"unknown MAVLink message type: {type}")
    return func(*args, **kwds)


def create_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: Union[str, Callable] = "ardupilotmega",
) -> MessageChannel[Tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given connection, and does the parsing of MAVLink
    messages automatically. The channel will accept and yield
    tuples containing a MAVLinkMessage_ object and a "network ID" that identifies
    a namespace within which all MAVLink system IDs are considered to be
    unique.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    mavlink_factory = get_mavlink_factory(dialect)
    mavlink = mavlink_factory()

    network = MAVLinkNetworkSpecification(id="")

    def parser(data: bytes) -> List[Tuple[MAVLinkMessage, str]]:
        return [(message, network.id) for message in mavlink.parse_buffer(data)]

    def encoder(spec_and_network_id: Tuple[MAVLinkMessage, str]) -> bytes:
        # TODO(ntamas): use a separate MAVLink object by network, each
        # pre-configured for the appropriate system and component ID, and then
        # use the appropriate MAVLink object to pack the message
        spec, _ = spec_and_network_id
        type, kwds = spec
        message = create_mavlink_message(mavlink, type, **kwds)
        result = message.pack(mavlink)

        # Bookkeeping copied from the MAVLink.send() method
        mavlink.seq = (mavlink.seq + 1) % 256
        mavlink.total_packets_sent += 1
        mavlink.total_bytes_sent += len(result)

        return result

    return MessageChannel(connection, parser=parser, encoder=encoder)
