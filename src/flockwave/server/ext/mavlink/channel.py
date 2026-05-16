"""Functions related to constructing MessageChannel_ instances from connection
objects to receive and send MAVLink messages.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from time import time
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from flockwave.channels import (
    BroadcastMessageChannel,
    MessageChannel,
)
from flockwave.connections import (
    Connection,
    RWConnection,
    StreamConnectionBase,
)
from flockwave.logger import Logger
from flockwave.protocols.mavlink.introspection import import_dialect

from .enums import MAVComponent
from .signing import MAVLinkSigningConfiguration, SignatureTimestampSynchronizer

if TYPE_CHECKING:
    from flockwave.protocols.mavlink.types import (
        MinimalMAVLinkFactory,
        MinimalMAVLinkInterface,
    )

    from .types import MAVLinkMessage, MAVLinkMessageSpecification

__all__ = (
    "create_mavlink_message_channel",
    "encode_mavlink_message_from_spec",
    "use_mavlink_message_channel_factory",
)


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

    SHOW_CONTROL: ClassVar[str] = "_show_control"
    """Alias of the channel that should be used for sending drone show control messages
    (start time, authorization, time axis etc).
    """


def create_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    dialect: str = "ardupilotmega",
    network_id: str = "",
    system_id: int = 255,
    link_ids: dict[Connection, int] | None = None,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
) -> MessageChannel[tuple[MAVLinkMessage, str], Any]:
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

    mavlink_factory = _get_mavlink_factory(
        dialect, system_id, link_id=link_id, signing=signing
    )

    log_extra = {"id": network_id}

    for factory in _message_channel_factories:
        try:
            channel = factory(
                connection,
                log,
                mavlink_factory=mavlink_factory,
            )
            break
        except ConnectionNotSupportedError:
            continue
    else:
        raise RuntimeError(
            f"Connection type not supported for MAVLink: {connection.__class__!r}"
        )

    if signing.enabled:
        if signing.allow_unsigned:
            log.info(
                f"Configured MAVLink signing with link ID = {link_id}, allowing "
                f"unsigned incoming messages",
                extra=log_extra,
            )
        else:
            log.info(
                f"Configured MAVLink signing with link ID = {link_id}",
                extra=log_extra,
            )

    return channel


class ConnectionNotSupportedError(RuntimeError):
    """Exception raised when a MAVLink message channel factory is asked to
    create a channel for a connection that it does not support.
    """

    pass


class MAVLinkMessageChannelFactory(Protocol):
    """Calling convention specification for functions that can be registered
    to construct a MAVLink message channel from an underlying connection
    object.
    """

    def __call__(
        self,
        connection: Connection,
        log: Logger,
        *,
        mavlink_factory: MinimalMAVLinkFactory,
    ) -> MessageChannel[tuple[MAVLinkMessage, str], Any]:
        """Creates a MAVLink message channel for the given connection.

        Args:
            connection: the underlying connection to read data from and write data to
            log: the logger on which any error messages and warnings should be logged
            mavlink_factory: a factory function that can be called with no arguments
                to create a new MAVLink object that keeps track of sequence numbers.

        Returns:
            a MAVLink message channel that reads data from and writes data to the
            given connection

        Raises:
            ConnectionNotSupportedError: if the given connection is not supported
                by this factory
        """
        ...


@contextmanager
def use_mavlink_message_channel_factory(
    factory: MAVLinkMessageChannelFactory,
) -> Iterator[None]:
    """Context manager that registers a new MAVLink message channel factory that
    will be used by `create_mavlink_message_channel()` to create channels for
    connections.

    The newly registered factory will be tried first, before any of the built-in
    factories. This allows other extensions to provide implementations of
    alternative Connection_ instances from which MAVLink messages can be read.

    The message channel factory is automatically unregistered when the context
    manager exits.

    Args:
        factory: the factory function to register
    """
    try:
        _message_channel_factories.insert(0, factory)
        yield
    finally:
        try:
            _message_channel_factories.remove(factory)
        except ValueError:
            pass


def encode_mavlink_message_from_spec(
    spec: MAVLinkMessageSpecification, mavlink: MinimalMAVLinkInterface
) -> bytes:
    """Encodes a MAVLink message given a message specification and a MAVLink
    object. Sequence numbers are generated from the current state of the
    MAVLink object. The internal state variables are also updated as needed.

    This function is essentially a building block for message channel factories
    where it can be used in the encoder function.
    """
    type, kwds = spec

    mavlink_version = kwds.pop("_mavlink_version", 2)

    message = _create_mavlink_message(mavlink, type, **kwds)
    result: bytes = message.pack(mavlink, force_mavlink1=mavlink_version < 2)

    # Bookkeeping copied from the MAVLink.send() method
    mavlink.seq = (mavlink.seq + 1) % 256
    mavlink.total_packets_sent += 1
    mavlink.total_bytes_sent += len(result)

    return result


def _create_stream_based_mavlink_message_channel(
    connection: Connection,
    log: Logger,
    *,
    mavlink_factory: MinimalMAVLinkFactory,
) -> MessageChannel[tuple[MAVLinkMessage, str], bytes]:
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
    if not isinstance(connection, StreamConnectionBase):
        raise ConnectionNotSupportedError()

    mavlink = mavlink_factory()

    def parser(data: bytes) -> list[tuple[MAVLinkMessage, str]]:
        # Parse the MAVLink messages from the buffer. mavlink.parse_buffer()
        # may occasionally return None so make sure we handle that gracefully.
        messages = mavlink.parse_buffer(data) or ()
        return [(message, "") for message in messages]

    def encoder(spec_and_address: tuple[MAVLinkMessageSpecification, Any]) -> bytes:
        spec, _ = spec_and_address
        return encode_mavlink_message_from_spec(spec, mavlink)

    return BroadcastMessageChannel(
        connection, parser=parser, encoder=encoder, broadcast_encoder=encoder
    )


def _create_datagram_based_mavlink_message_channel(
    connection: Connection, log: Logger, *, mavlink_factory: MinimalMAVLinkFactory
) -> MessageChannel[tuple[MAVLinkMessage, str], tuple[bytes, Any]]:
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
    if not isinstance(connection, RWConnection):
        raise ConnectionNotSupportedError()

    connection = cast("RWConnection[tuple[bytes, Any], tuple[bytes, Any]]", connection)

    # We will need one MAVLink object per _address_ that we are talking to
    # for two reasons:
    #
    # 1) each address needs a unique sequence number counter
    # 2) each UAV may send MAVLink messages in a way that the MAVLink message
    #    boundaries do not align with the packet boundaries of the transport
    #    medium (e.g., UDP packets). Therefore, we need to keep track of partially
    #    parsed MAVLink messages between datagrams.
    #
    # There are still some unexpected issues with this approach, though. Each
    # drone is accessible via _two_ addresses: its own unicast address and the
    # broadcast address. The broadcast address uses a common sequence number for
    # all destinations. The unicast address uses its own sequence number. On the
    # drone's side there is no way to distiguish between the two, so a broadcast
    # packet followed by a unicast packet (or vice versa) will introduce gaps
    # in the sequence numbering, making the drone overestimate the packet loss
    # on the link.
    #
    # Broadcast vs unicast packets also cause problems with MAVLink packet
    # signing as their timestamp counters are not synchronized by default.
    # Since all the MAVLink objects constructed here will use the same link ID
    # in the signatures, they need to behave as a "hivemind" -- their timestamp
    # counters must be synchronized such that whenever one of the counters is
    # incremented, the remaining timestamps must also be incremented. Therefore,
    # we have a singleton timestamp synchronizer object at the top level of this
    # module that is then patched into each MAVLink object created here.

    mavlink_by_address = defaultdict(mavlink_factory)

    # Connection is a datagram-based connection so we will be receiving
    # full messages along with the addresses they were sent from
    def parser(data_and_address: tuple[bytes, Any]) -> list[tuple[MAVLinkMessage, Any]]:
        data, address = data_and_address

        mavlink = mavlink_by_address[address]
        messages = mavlink.parse_buffer(data) or ()

        return [(message, address) for message in messages]

    def encoder(
        spec_and_address: tuple[MAVLinkMessageSpecification, Any],
    ) -> tuple[bytes, Any]:
        spec, address = spec_and_address
        mavlink = mavlink_by_address[address]
        result = encode_mavlink_message_from_spec(spec, mavlink)
        return (result, address)

    return BroadcastMessageChannel(
        connection, parser=parser, encoder=encoder, broadcast_encoder=encoder
    )


_message_channel_factories: list[MAVLinkMessageChannelFactory] = [
    # The order is important here. First we need to check whether the connection
    # is a stream-based (TCP-like) connection, and only if that fails we
    # should try to create a datagram-based (UDP-like) connection. This is
    # because all StreamConnectionBase instances are also RWConnection instances,
    # so if we tried the datagram-based factory first, it would always succeed
    # for stream-based connections as well.
    _create_stream_based_mavlink_message_channel,
    _create_datagram_based_mavlink_message_channel,
]
"""List of registered MAVLink message channel factories."""


_signature_timestamp_synchronizer = SignatureTimestampSynchronizer()
"""Object to synchronize MAVLink signing timestamps created between different
MAVLink networks.
"""


def _create_mavlink_message(
    link: MinimalMAVLinkInterface, _type: str, *args, **kwds
) -> MAVLinkMessage:
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


def _get_mavlink_factory(
    dialect: str = "ardupilotmega",
    system_id: int = 255,
    component_id: int = MAVComponent.MISSIONPLANNER,
    *,
    link_id: int = 0,
    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED,
) -> MinimalMAVLinkFactory:
    """Constructs a function that can be called with no arguments and that will
    construct a new MAVLink parser and message factory.

    Parameters:
        dialect: the name of the MAVLink dialect that will be used by the parser
        system_id: the source MAVLink system ID
        component_id: the source MAVLink component ID
        link_id: numeric link ID, used for signing MAVLink messages. Must
            be configured properly for signing to work; ignored for unsigned
            channels.
        signing: whether outbound messages should be signed and inbound messages
            should be rejectd when unsigned
    """
    module = import_dialect(dialect)

    def factory() -> MinimalMAVLinkInterface:
        """Creates a new MAVLink parser and message factory."""
        # Use robust parsing so we don't freak out if we see some noise on the
        # line
        link: MinimalMAVLinkInterface = module.MAVLink(
            None, srcSystem=system_id, srcComponent=component_id
        )
        link.robust_parsing = True

        if signing.enabled:
            link.signing.link_id = min(link_id, 255)
            link.signing.secret_key = signing.key
            link.signing.timestamp = _get_initial_timestamp_for_signing()
            link.signing.sign_outgoing = True
            link.signing.allow_unsigned_callback = (
                _allow_all_unsigned_messages
                if signing.allow_unsigned
                else _allow_only_basic_unsigned_messages
            )
            _signature_timestamp_synchronizer.patch(link)

        return link

    return factory


def _get_initial_timestamp_for_signing() -> int:
    """Returns a timestamp based on the system clock that is suitable for usage
    in a MAVLink signing context.
    """
    # Offset = 1420070400, i.e. the number of seconds between the current time
    # and 1st January 2015 GMT. The multiplier converts to units of 10 usec.
    return max(int((time() - 1420070400) * 100000), 0)


def _allow_all_unsigned_messages(mav: MinimalMAVLinkInterface, msg_id: int) -> bool:
    """Callback function for MAVLink connections that allow them to accept
    all unsigned messages.
    """
    return True


def _allow_only_basic_unsigned_messages(
    mav: MinimalMAVLinkInterface, msg_id: int
) -> bool:
    """Callback function for MAVLink connections that allow them to accept
    only specific unsigned messages that may originate from sources that do
    not support MAVLink signing.
    """
    return msg_id == 109  # 109 -- RADIO_STATUS
