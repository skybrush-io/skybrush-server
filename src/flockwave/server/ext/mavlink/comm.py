"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from importlib import import_module
from os import devnull
from typing import Any, Callable, List, Union, Tuple

from flockwave.channels import MessageChannel
from flockwave.connections import Connection
from flockwave.logger import Logger

from flockwave.server.comm import CommunicationManager


__all__ = ("create_communication_manager",)

#: Type specification for messages parsed by the MAVLink parser. Unfortunately
#: we cannot refer to an exact Python class here because that depends on the
#: dialoect that we will be parsing
MAVLinkMessage = Any


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

    def factory(fp):
        # Use robust parsing so we don't freak out if we see some noise on the
        # line
        parser = module.MAVLink(fp)
        parser.robust_parsing = True
        return parser

    return factory


def create_communication_manager() -> CommunicationManager[Any, Any]:
    """Creates a communication manager instance for the extension."""
    return CommunicationManager(channel_factory=create_mavlink_message_channel)


def create_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: Union[str, Callable] = "ardupilotmega",
) -> MessageChannel[Tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given connection, and does the parsing of MAVLink
    messages automatically. The channel will accept and yield
    tuples containing a MAVLinkMessage_ object and a "swarm ID" that identifies
    a namespace within which all MAVLink system IDs are considered to be
    unique.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    mavlink_parser_factory = get_mavlink_parser_factory(dialect)
    mavlink_parser = mavlink_parser_factory(open(devnull, "wb"))

    def parser(data: bytes) -> List[Tuple[MAVLinkMessage, str]]:
        return [(message, "default") for message in mavlink_parser.parse_buffer(data)]

    return MessageChannel(connection, parser=parser, encoder=None)
