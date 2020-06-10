"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from collections import defaultdict
from importlib import import_module
from typing import Any, Callable, List, Union, Tuple

from flockwave.channels import MessageChannel
from flockwave.connections import Connection, StreamConnection
from flockwave.logger import Logger

from flockwave.server.comm import CommunicationManager

from .enums import MAVComponent
from .types import MAVLinkMessage, MAVLinkMessageSpecification


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
    messages automatically. The channel will yield MAVLinkMessage_ objects
    and accept MAVLink message specifications, which are essentially tuples
    consisting of a MAVLink message type and the corresponding arguments. This
    is because the actual message is constructed by the encoder of the channel.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    if isinstance(connection, StreamConnection):
        return _create_stream_based_mavlink_message_channel(
            connection, log, dialect=dialect
        )
    else:
        return _create_datagram_based_mavlink_message_channel(
            connection, log, dialect=dialect
        )


def _create_stream_based_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: Union[str, Callable] = "ardupilotmega",
) -> MessageChannel[Tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given stream-based connection, and does the parsing
    of MAVLink messages automatically.

    The channel will yield pairs of a MAVLinkMessage_ object and an empty string
    as an "address" to make the interface similar to the datagram-based MAVLink
    channels. (In other words, the caller does not have to know whether she is
    working with a datagram-based or a stream-based channel).

    The channel accepts pairs of MAVLink message specification and an empty
    string. The message specifications are essentially tuples consisting of
    a MAVLink message type and the corresponding arguments. This is needed
    because the actual message is constructed by the encoder of the channel
    to ensure continuity of sequence numbers.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    mavlink_factory = get_mavlink_factory(dialect)
    mavlink = mavlink_factory()

    def parser(data: bytes) -> List[Tuple[MAVLinkMessage, Any]]:
        # TODO(ntamas): sometimes mavlink.parse_buffer() returns None, how
        # can that happen?
        messages = mavlink.parse_buffer(data) or ()
        return [(message, "") for message in messages]

    def encoder(spec_and_address: Tuple[MAVLinkMessageSpecification, Any]) -> bytes:
        spec, _ = spec_and_address
        type, kwds = spec
        message = create_mavlink_message(mavlink, type, **kwds)
        result = message.pack(mavlink)
        _notify_mavlink_packet_sent(mavlink, result)
        return result

    return MessageChannel(connection, parser=parser, encoder=encoder)


def _create_datagram_based_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: Union[str, Callable] = "ardupilotmega",
) -> MessageChannel[Tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given datagram-based connection, and does the parsing
    of MAVLink messages automatically. The channel will yield pairs of
    MAVLinkMessage_ objects and the addresses they were sent from, and accept
    pairs of MAVLink message specifications and the addresses they should be
    sent to. The message specifications are are essentially tuples consisting of
    a MAVLink message type and the corresponding arguments. This is needed
    because the actual message is constructed by the encoder of the channel
    to ensure continuity of sequence numbers.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
    """
    mavlink_factory = get_mavlink_factory(dialect)

    # We will need one MAVLink object per _address_ that we are talking to
    # because each address needs a unique sequence number counter.
    #
    # Should this become a performance bottleneck: maybe we can do away with
    # one MAVLink parser and a separate dict that keeps track of the sequence
    # numbers? This could be tricky because the MAVLink checksum depends on the
    # sequence number so we need to feed the _current_ sequence number into the
    # MAVLink object _before_ calling pack().
    mavlink_by_address = defaultdict(mavlink_factory)

    # Connection is a datagram-based connection so we will be receiving
    # full messages along with the addresses they were sent from
    def parser(data_and_address: Tuple[bytes, Any]) -> List[Tuple[MAVLinkMessage, Any]]:
        data, address = data_and_address

        mavlink = mavlink_by_address[address]
        messages = mavlink.parse_buffer(data) or ()

        return [(message, address) for message in messages]

    def encoder(
        spec_and_address: Tuple[MAVLinkMessageSpecification, Any]
    ) -> Tuple[bytes, Any]:
        spec, address = spec_and_address

        type, kwds = spec
        mavlink = mavlink_by_address[address]

        message = create_mavlink_message(mavlink, type, **kwds)
        result = message.pack(mavlink)

        _notify_mavlink_packet_sent(mavlink, result)

        return (result, address)

    return MessageChannel(connection, parser=parser, encoder=encoder)


def _notify_mavlink_packet_sent(mavlink, packet: bytes) -> None:
    # Bookkeeping copied from the MAVLink.send() method
    mavlink.seq = (mavlink.seq + 1) % 256
    mavlink.total_packets_sent += 1
    mavlink.total_bytes_sent += len(packet)
