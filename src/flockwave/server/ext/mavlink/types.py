"""Types commonly used throughout the MAVLink module."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Iterable,
    Optional,
    Protocol,
    Sequence,
    Union,
    TYPE_CHECKING,
    overload,
)

from .enums import MAVSeverity
from .rssi import RSSIMode
from .signing import MAVLinkSigningConfiguration

if TYPE_CHECKING:
    from .driver import MAVLinkUAV


__all__ = (
    "MAVLinkFlightModeNumbers",
    "MAVLinkMessage",
    "MAVLinkMessageSpecification",
    "MAVLinkNetworkSpecification",
    "spec",
)


MAVLinkFlightModeNumbers = tuple[int, int, int]
"""Type specification for a (base mode, main mode, submode) flight mode triplet
used in MAVLink.
"""

MAVLinkMessage = Any
"""Type specification for messages parsed by the MAVLink parser. Unfortunately
we cannot refer to an exact Python class here because that depends on the
dialect that we will be parsing.
"""

MAVLinkMessageMatcher = Optional[
    Union[dict[str, Any], Callable[[MAVLinkMessage], bool]]
]
"""Type specification for MAVLink message matchers. A message matcher is either
`None` (meaning to match all messages), a dictionary containing the required
field name-value pairs in a message that we need to consider the message to
be a match, or a callable that takes a MAVLinkMessage and returns `True` if
the message is a match.
"""

MAVLinkMessageSpecification = tuple[str, dict[str, Any]]
"""Type specification for MAVLink message specifications. A message specification
is a tuple containing the message type and a dictionary of field name-value
pairs. These are used to construct new MAVLink messages.
"""

PacketBroadcasterFn = Callable[..., Awaitable[None]]
"""Type specification for the broadcast_packet() function of a MAVLinkNetwork object."""


class PacketSenderFn(Protocol):
    """Type specification for the send_packet() function of a MAVLinkNetwork_
    bject.

    See the documentation in `MAVLinkNetwork.send_packet()` for more details.
    """

    @overload
    def __call__(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        *,
        channel: Optional[str] = None,
    ) -> Awaitable[None]: ...

    @overload
    def __call__(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        *,
        wait_for_response: tuple[str, MAVLinkMessageMatcher],
        channel: Optional[str] = None,
    ) -> Awaitable[MAVLinkMessage]: ...

    @overload
    def __call__(
        self,
        spec: MAVLinkMessageSpecification,
        target: MAVLinkUAV,
        *,
        wait_for_one_of: dict[str, MAVLinkMessageSpecification],
        channel: Optional[str] = None,
    ) -> Awaitable[tuple[str, MAVLinkMessage]]: ...


class UAVBoundPacketSenderFn(Protocol):
    """Type specification for the send_packet() function of a MAVLinkUAV_
    object.

    See the documentation in `MAVLinkUAV.send_packet()` for more details.
    """

    @overload
    def __call__(
        self,
        spec: MAVLinkMessageSpecification,
        *,
        channel: Optional[str] = None,
    ) -> Awaitable[None]: ...

    @overload
    def __call__(
        self,
        spec: Optional[MAVLinkMessageSpecification],
        *,
        wait_for_response: tuple[str, MAVLinkMessageMatcher],
        channel: Optional[str] = None,
    ) -> Awaitable[MAVLinkMessage]: ...

    @overload
    def __call__(
        self,
        spec: Optional[MAVLinkMessageSpecification],
        *,
        wait_for_one_of: dict[str, MAVLinkMessageSpecification],
        channel: Optional[str] = None,
    ) -> Awaitable[tuple[str, MAVLinkMessage]]: ...


class _MAVLinkMessageSpecificationFactory:
    """Convenience constructor for MAVLink message specifications."""

    def __init__(self):
        self._cache = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError

        name = name.upper()
        func = self._cache.get(name)
        if not func:
            self._cache[name] = func = partial(self._match, name)
        return func

    @staticmethod
    def _match(name, *args, **kwds):
        if args:
            if kwds:
                raise RuntimeError(
                    "mixing function matchers with keyword arguments not supported"
                )
            if len(args) > 1:
                raise RuntimeError("only one matcher function is supported")
            return (name, args[0])  # type: ignore
        else:
            return (name, kwds)


spec = _MAVLinkMessageSpecificationFactory()


@dataclass(frozen=True)
class MAVLinkStatusTextTargetSpecification:
    """Object that specifies where to send MAVLink STATUSTEXT messages
    originating from a MAVLink network.
    """

    server: int = int(MAVSeverity.NOTICE)
    """Maximum MAVLink severity level of messages to be forwarded to the
    server. MAVLink messages with a severity smaller than or equal to this
    value will be printed on the server console. Set to -1 to turn off the
    forwarding of MAVLink messages to the server console.
    """

    client: int = int(MAVSeverity.DEBUG)
    """Maximum MAVLink severity level of messages to be forwarded to the
    client. MAVLink messages with a severity smaller than or equal to this
    value will be forwarded to connected clients. Set to -1 to turn off the
    forwarding of MAVLink messages to the server console.
    """

    log_prearm: bool = False
    """Whether to log STATUSTEXT messages that represent the result of a failing
    pre-arm check. These messages are quite common and can be inspected by
    other means because they are stored in the appropriate UAV object, hence
    they are hidden by default.
    """

    DEFAULT: ClassVar[MAVLinkStatusTextTargetSpecification]
    """Special instance to denote the default settings."""

    @classmethod
    def from_json(cls, obj):
        """Constructs a MAVLink STATUSTEXT target specification from its JSON
        representation.
        """
        server_level: int = int(MAVSeverity.NOTICE)
        client_level: int = int(MAVSeverity.DEBUG)
        log_prearm: bool = False

        if isinstance(obj, dict):
            # New-style representation, keys mapping to severity levels
            level = cls._severity_from_json(obj.get("server"))
            if level is not None:
                server_level = level

            level = cls._severity_from_json(obj.get("client"))
            if level is not None:
                client_level = level

            log_prearm = bool(obj.get("log_prearm", False))

        elif isinstance(obj, Iterable):
            # Old-style representation, array containing the enabled targets
            enabled = set(obj)
            if "server" not in enabled:
                server_level = -1
            if "client" not in enabled:
                client_level = -1

        return cls(server=server_level, client=client_level, log_prearm=log_prearm)

    @staticmethod
    def _severity_from_json(x: Any) -> Optional[int]:
        if isinstance(x, int):
            return x
        elif isinstance(x, str):
            return int(getattr(MAVSeverity, x.upper(), None))  # type: ignore
        else:
            return None

    @staticmethod
    def _severity_to_json(x: int) -> Union[int, str]:
        try:
            severity = MAVSeverity(x)
        except Exception:
            return x
        else:
            return severity.name.lower()

    @property
    def json(self):
        """Returns the JSON representation of the specification."""
        return {
            "server": self._severity_to_json(self.server),
            "client": self._severity_to_json(self.client),
            "log_prearm": bool(self.log_prearm),
        }


MAVLinkStatusTextTargetSpecification.DEFAULT = MAVLinkStatusTextTargetSpecification()


@dataclass
class MAVLinkNetworkSpecification:
    """Parameter specification for a single MAVLink network."""

    id: str
    """Unique identifier of this MAVLink network that the server can use as a
    primary key.
    """

    system_id: int = 255
    """MAVLink system ID reserved for the ground station (the Skybrush server)."""

    id_format: str = "{1}{0}"
    """Python format string that receives the MAVLink system ID of a drone
    and the network ID, and returns its preferred formatted identifier that
    is used when the drone is registered in the server.
    """

    id_offset: int = 0
    """Offset to add to the system IDs of drones in the same network before
    they are sent to the ID formatter function.
    """

    network_size: int = 250
    """Number of system IDs reserved for drones in this network. The server
    will treat system IDs from 1 to ``network_size`` (inclusive) as drones in
    this network.
    """

    connections: list[str] = field(default_factory=list)
    """The connections that the MAVLink network will consist of. A MAVLink
    network may have one or more connections where MAVLink messages are
    received and sent, but a system ID appearing on one of the networks
    identifies the _same_ device as the same system ID on another network.
    In most cases, one link is the primary one and the rest are used as
    "backups".

    The list may contain any values that are accepted by the
    `create_connection()` function.
    """

    routing: dict[str, list[int]] = field(default_factory=dict)
    """Specifies where certain types of packets should be routed if the
    network has multiple connections.
    """

    rssi_mode: RSSIMode = RSSIMode.RADIO_STATUS
    """Specifies how the network will derive the RSSI (received signal strength
    indicator) value of its own drones.
    """

    signing: MAVLinkSigningConfiguration = MAVLinkSigningConfiguration.DISABLED
    """Specifies whether MAVLink packets should be signed and whether the
    connection should accept unsigned MAVLink packets.
    """

    statustext_targets: MAVLinkStatusTextTargetSpecification = field(
        default=MAVLinkStatusTextTargetSpecification.DEFAULT
    )
    """Specifies where to send the contents of MAVLink status text messages
    originating from this network. This property must be a set containing
    'server' to forward the messages to the server log and/or 'client' to
    forward the messages to the connected clients in SYS-MSG messages.
    """

    packet_loss: float = 0
    """Whether to simulate packet loss in this network by randomly dropping
    received and sent messages. Zero means the normal behaviour, otherwise
    it is interpreted as the probability of a lost MAVLink message.
    """

    use_broadcast_rate_limiting: bool = False
    """Whether to apply a small delay between consecutive broadcast packets
    to work around packet loss issues on links without proper flow control in
    the chain. Typically you can leave this at ``False``; set it to ``True``
    only if you have packet loss problems and you suspect that it is due to
    lack of flow control so slowing down the packet sending rate in bursts would
    help.
    """

    @classmethod
    def from_json(cls, obj, id: Optional[str] = None):
        """Constructs a MAVLink network specification from its JSON
        representation.
        """
        result = cls(id=id if id is not None else obj["id"])

        if "system_id" in obj:
            result.system_id = int(obj["system_id"])

        if "id_format" in obj:
            result.id_format = obj["id_format"]

        if "id_offset" in obj:
            result.id_offset = int(obj["id_offset"])

        if "network_size" in obj:
            result.network_size = int(obj["network_size"])

        if "connections" in obj:
            result.connections = obj["connections"]

        if "packet_loss" in obj:
            result.packet_loss = float(obj["packet_loss"])

        if "routing" in obj and isinstance(obj["routing"], dict):
            result.routing.clear()
            result.routing.update(
                {k: cls._process_routing_entry(v) for k, v in obj["routing"].items()}
            )

        if "rssi_mode" in obj and isinstance(obj["rssi_mode"], str):
            result.rssi_mode = RSSIMode(obj["rssi_mode"])

        if "signing" in obj and isinstance(obj["signing"], dict):
            result.signing = MAVLinkSigningConfiguration.from_json(obj["signing"])

        if "statustext_targets" in obj:
            result.statustext_targets = MAVLinkStatusTextTargetSpecification.from_json(
                obj["statustext_targets"]
            )

        if "use_broadcast_rate_limiting" in obj:
            result.use_broadcast_rate_limiting = bool(
                obj["use_broadcast_rate_limiting"]
            )

        return result

    @property
    def json(self):
        """Returns the JSON representation of the network specification."""
        return {
            "id": self.id,
            "id_format": self.id_format,
            "id_offset": self.id_offset,
            "network_size": self.network_size,
            "system_id": self.system_id,
            "connections": self.connections,
            "packet_loss": self.packet_loss,
            "routing": self.routing,
            "rssi_mode": self.rssi_mode.value,
            "signing": self.signing,
            "statustext_targets": self.statustext_targets,
            "use_broadcast_rate_limiting": bool(self.use_broadcast_rate_limiting),
        }

    @staticmethod
    def _process_routing_entry(entry: Union[int, str, Sequence[int]]) -> list[int]:
        """Helper function for processing entries in the ``routing`` configuration
        key and constructor parameter.
        """
        if isinstance(entry, int):
            return [entry]
        elif isinstance(entry, str):
            return [int(x) for x in entry.strip().split()]
        else:
            return [int(x) for x in entry]
