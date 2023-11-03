"""Communication manager that facilitates communication between a MAVLink-based
UAV and the ground station via some communication link.
"""

from collections import defaultdict
from compose import compose
from functools import partial
from importlib import import_module
from time import time
from typing import Any, Callable, ClassVar, Optional, Union

from flockwave.channels import MessageChannel, create_lossy_channel
from flockwave.connections import Connection, StreamConnectionBase
from flockwave.logger import Logger
from flockwave.networking import format_socket_address

from flockwave.server.comm import NO_BROADCAST_ADDRESS, CommunicationManager

from .enums import MAVComponent
from .signing import MAVLinkSigningConfiguration
from .types import MAVLinkMessage, MAVLinkMessageSpecification


__all__ = ("create_communication_manager",)


class Channel:
    """Enum class to contain string aliases for the channels where the primary
    and the RTK traffic should be sent on.
    """

    PRIMARY: ClassVar[str] = "_primary"
    """Constant to denote packets that we wish to send on the primary channel of the
    network (typically wifi). This will be added as an alias to the first
    connection in each network.
    """

    SECONDARY: ClassVar[str] = "_secondary"
    """Constant to denote packets that we wish to send on the secondary channel of the
    network (typically a long-range radio). This will be added as an alias to the second
    connection in each network.
    """

    RTK: ClassVar[str] = "_rtk"
    """Alias of the channel that should be used for sending RTK connections"""

    RC: ClassVar[str] = "_rc"
    """Alias of the channel that should be used for sending RC overrides"""


def _get_timestamp_for_signing() -> int:
    """Returns a timestamp based on the system clock that is suitable for using
    in a MAVLink signing context.
    """
    # Offset = 1420070400, i.e. the number of seconds between the current time
    # and 1st January 2015 GMT. The multiplier converts to units of 10 usec.
    return max(int((time() - 1420070400) * 100000), 0)


MAVLinkFactory = Callable[[], Any]


def _allow_all_unsigned_messages(self, msg_id: int) -> bool:
    """Callback function for MAVLink connections that allow them to accept
    all unsigned messages.
    """
    return True


def _allow_only_basic_unsigned_messages(self, msg_id: int) -> bool:
    """Callback function for MAVLink connections that allow them to accept
    only specific unsigned messages that may originate from sources that do
    not support MAVLink signing.
    """
    return msg_id == 109  # 109 -- RADIO_STATUS


def get_mavlink_factory(
    dialect: Union[str, Callable] = "ardupilotmega",
    system_id: int = 255,
    component_id: int = MAVComponent.MISSIONPLANNER,
    *,
    link_id: int = 0,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
) -> MAVLinkFactory:
    """Constructs a function that can be called with no arguments and that will
    construct a new MAVLink parser and message factory.

    Parameters:
        dialect: the name of the MAVLink dialect that will be used by the
            parser. When it is a callable, it is returned intact.
        system_id: the source MAVLink system ID
        component_id: the source MAVLink component ID
        link_id: numeric link ID, used for signing MAVLink messages. Must
            be configured properly for signing to work; ignored for unsigned
            channels.
        signing: whether outbound messages should be signed and inbound messages
            should be rejectd when unsigned
    """
    if callable(dialect):
        return dialect

    module = import_module(f"flockwave.protocols.mavlink.dialects.v20.{dialect}")

    def factory():
        """Creates a new MAVLink parser and message factory."""
        # Use robust parsing so we don't freak out if we see some noise on the
        # line
        link = module.MAVLink(None, srcSystem=system_id, srcComponent=component_id)
        link.robust_parsing = True

        if signing.enabled:
            link.signing.link_id = min(link_id, 255)
            link.signing.secret_key = signing.key
            link.signing.timestamp = _get_timestamp_for_signing()
            link.signing.sign_outgoing = True
            link.signing.allow_unsigned_callback = (
                _allow_all_unsigned_messages
                if signing.allow_unsigned
                else _allow_only_basic_unsigned_messages
            )

        return link

    return factory


def create_communication_manager(
    *,
    packet_loss: float = 0,
    system_id: int = 255,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
    use_broadcast_rate_limiting: bool = False,
) -> CommunicationManager[MAVLinkMessageSpecification, Any]:
    """Creates a communication manager instance for a single network managed
    by the extension.

    Parameters:
        packet_loss: simulated packet loss probability; zero means normal
            behaviour
        system_id: the system ID to use in MAVLink messages sent by this
            communication manager
        signing: specifies how to handle signed MAVLink messages in both the
            incoming and the outbound direction
        use_broadcast_rate_limiting: whether to apply a small delay after
            sending each broadcast packet; this can be used to counteract
            rate limiting problems if there are any. Typically you can leave
            this setting at `False` unless you see lots of lost broadcast
            packets.
    """
    # Create a dictionary to cache link IDs to existing connections so we can
    # keep on using the same link ID for the same connection even if it is
    # closed and re-opened later
    link_ids: dict[Connection, int] = {}
    channel_factory = partial(
        create_mavlink_message_channel,
        signing=signing,
        link_ids=link_ids,
        system_id=system_id,
    )

    if packet_loss > 0:
        channel_factory = compose(
            partial(create_lossy_channel, loss_probability=packet_loss), channel_factory
        )

    manager = CommunicationManager(
        channel_factory=channel_factory,
        format_address=format_mavlink_channel_address,
    )

    if use_broadcast_rate_limiting:
        manager.broadcast_delay = 0.005

    return manager


def create_mavlink_message(link, _type: str, *args, **kwds) -> MAVLinkMessage:
    """Creates a MAVLink message from the methods of a MAVLink object received
    from the low-level `pymavlink` library.

    Parameters:
        link: the low-level MAVLink object
        _type: the type of the message to construct. It will be lower-cased
            automatically and it will be used to look up a function named
            `<type>_encode` on the low-level MAVLink object

    Additional positional and keyword arguments are forwarded to the appropriate
    function of the low-level MAVLink object.

    Returns:
        the MAVLink message as returned from the low-level MAVLink object
    """
    try:
        func = getattr(link, f"{_type.lower()}_encode")
    except AttributeError:
        raise ValueError(f"unknown MAVLink message type: {_type}") from None
    return func(*args, **kwds)


def create_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: Union[str, Callable] = "ardupilotmega",
    system_id: int = 255,
    link_ids: Optional[dict[Connection, int]] = None,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
) -> MessageChannel[tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given connection, and does the parsing of MAVLink
    messages automatically. The channel will yield MAVLinkMessage_ objects
    and accept MAVLink message specifications, which are essentially tuples
    consisting of a MAVLink message type and the corresponding arguments. This
    is because the actual message is constructed by the encoder of the channel.

    Parameters:
        connection: the connection to read data from and write data to
        log: the logger on which any error messages and warnings should be logged
        dialect: the MAVLink dialect to use on the channel
        system_id: MAVLink source system ID to use on the channel for messages
            being sent over the link
        link_id: link identifier, to be used in MAVLink signing. Ignored for
            unsigned links
        signing: specifies whether outbound messages should be signed and
            inbound messages should be checked for a valid signature
    """
    if link_ids is not None:
        link_id = link_ids.get(connection, -1)
        if link_id < 0:
            link_id = link_ids[connection] = len(link_ids)
    else:
        link_id = 0

    mavlink_factory: MAVLinkFactory = get_mavlink_factory(
        dialect, system_id, link_id=link_id, signing=signing
    )

    if isinstance(connection, StreamConnectionBase):
        channel = _create_stream_based_mavlink_message_channel(
            connection, log, mavlink_factory=mavlink_factory
        )
    else:
        channel = _create_datagram_based_mavlink_message_channel(
            connection, log, mavlink_factory=mavlink_factory
        )

    if signing.enabled:
        if signing.allow_unsigned:
            log.info(
                f"Configured MAVLink signing on link ID = {link_id}, allowing "
                f"unsigned incoming messages"
            )
        else:
            log.info(f"Configured MAVLink signing on link ID = {link_id}")

    return channel


def _create_stream_based_mavlink_message_channel(
    connection: Connection, log: Logger, *, mavlink_factory: MAVLinkFactory
) -> MessageChannel[tuple[MAVLinkMessage, str]]:
    """Creates a bidirectional Trio-style channel that reads data from and
    writes data to the given stream-based connection, and does the parsing
    of MAVLink messages automatically.

    TCP and serial port based connections are both handled by this function.

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
    mavlink = mavlink_factory()

    def parser(data: bytes) -> list[tuple[MAVLinkMessage, Any]]:
        # Parse the MAVLink messages from the buffer. mavlink.parse_buffer()
        # may occasionally return None so make sure we handle that gracefully.
        messages = mavlink.parse_buffer(data) or ()
        return [(message, "") for message in messages]

    def encoder(spec_and_address: tuple[MAVLinkMessageSpecification, Any]) -> bytes:
        spec, _ = spec_and_address

        type, kwds = spec
        mavlink_version = kwds.pop("_mavlink_version", 2)

        message = create_mavlink_message(mavlink, type, **kwds)
        result = message.pack(mavlink, force_mavlink1=mavlink_version < 2)

        _notify_mavlink_packet_sent(mavlink, result)

        return result

    channel = MessageChannel(connection, parser=parser, encoder=encoder)
    channel.broadcast_address = ""

    return channel


def _create_datagram_based_mavlink_message_channel(
    connection: Connection, log: Logger, *, mavlink_factory: MAVLinkFactory
) -> MessageChannel[tuple[MAVLinkMessage, str]]:
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
    # We will need one MAVLink object per _address_ that we are talking to
    # for two reasons:
    #
    # 1) each address needs a unique sequence number counter
    # 2) each UAV may send MAVLink messages in a way that the MAVLink message
    #    boundaries do not align with the packet boundaries of the transport
    #    medium (e.g., UDP packets). Therefore, we need to keep track of partially
    #    parsed MAVLink messages between datagrams.
    mavlink_by_address = defaultdict(mavlink_factory)

    # Connection is a datagram-based connection so we will be receiving
    # full messages along with the addresses they were sent from
    def parser(data_and_address: tuple[bytes, Any]) -> list[tuple[MAVLinkMessage, Any]]:
        data, address = data_and_address

        mavlink = mavlink_by_address[address]
        messages = mavlink.parse_buffer(data) or ()

        return [(message, address) for message in messages]

    def encoder(
        spec_and_address: tuple[MAVLinkMessageSpecification, Any]
    ) -> tuple[bytes, Any]:
        spec, address = spec_and_address

        type, kwds = spec

        mavlink = mavlink_by_address[address]
        mavlink_version = kwds.pop("_mavlink_version", 2)

        message = create_mavlink_message(mavlink, type, **kwds)
        result = message.pack(mavlink, force_mavlink1=mavlink_version < 2)

        _notify_mavlink_packet_sent(mavlink, result)

        return (result, address)

    channel = MessageChannel(connection, parser=parser, encoder=encoder)
    channel.broadcast_address = getattr(
        connection, "broadcast_address", NO_BROADCAST_ADDRESS
    )

    return channel


def _notify_mavlink_packet_sent(mavlink, packet: bytes) -> None:
    # Bookkeeping copied from the MAVLink.send() method
    mavlink.seq = (mavlink.seq + 1) % 256
    mavlink.total_packets_sent += 1
    mavlink.total_bytes_sent += len(packet)


def format_mavlink_channel_address(address: Any) -> str:
    """Returns a formatted representation of the address of a MAVLink message
    channel.
    """
    try:
        return format_socket_address(address)
    except ValueError:
        return str(address)
