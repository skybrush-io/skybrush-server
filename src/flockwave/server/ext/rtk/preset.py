from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional

from flockwave.channels.types import Encoder, Parser
from flockwave.gps.rtcm import create_rtcm_encoder, create_rtcm_parser
from flockwave.gps.rtcm.packets import RTCMPacket, RTCMV2Packet, RTCMV3Packet
from flockwave.server.utils import constant

__all__ = ("RTKConfigurationPreset",)

#: Type specification for an RTK packet filter function
RTKPacketFilter = Callable[[RTCMPacket], bool]

#: Allowed packet formats in RTK streams
ALLOWED_FORMATS = set("auto rtcm2 rtcm3".split())


@dataclass
class RTKConfigurationPreset:
    """Data class representing an RTK configuration preset consisting of one or
    more RTK data sources and an optional packet filter to be executed on
    every received packet.
    """

    id: str
    title: Optional[str] = None
    format: str = "auto"
    sources: List[str] = field(default_factory=list)
    init: Optional[str] = None
    filter: Optional[RTKPacketFilter] = None

    @classmethod
    def from_json(cls, spec, *, id: Optional[str] = None):
        """Creates an RTK configuration preset object from its JSON
        representation used in configuration files.

        Parameters:
            spec: the JSON specification in the configuration file
            id: the ID of the preset, used when the preset is registered in a
                registry. It is also used as a fallback when no title is
                specified for the preset.
        """
        result = cls(id=id, title=str(spec["title"] if "title" in spec else id))

        if "format" in spec:
            result.format = str(spec["format"])
            if result.format not in ALLOWED_FORMATS:
                raise ValueError(f"Invalid RTK packet format: {result.format!r}")

        if "sources" in spec:
            sources = spec["sources"]
        elif "source" in spec:
            # source is an alias to sources
            sources = spec["source"]
        else:
            sources = []

        if not isinstance(sources, list):
            sources = [sources]

        for source in sources:
            result.add_source(source)

        if "init" in spec:
            init = spec["init"]
            result.init = init if isinstance(init, bytes) else str(init).encode("utf-8")

        if "filter" in spec:
            result.filter = create_filter_function(**spec["filter"])

        return result

    def add_source(self, source: str) -> None:
        """Adds a new RTK data source to this preset.

        Parameters:
            source: the RTK data source; anything that is accepted by
                ``create_connection()``.
        """
        self.sources.append(source)

    def accepts(self, packet: RTCMPacket) -> bool:
        """Returns whether the given RTCM packet would be accepted by the filters
        specified in this preset.
        """
        return self.filter is None or self.filter(packet)

    def create_encoder(self) -> Encoder[RTCMPacket, bytes]:
        """Creates an RTCM message encoder for this preset."""
        return create_rtcm_encoder("rtcm3" if self.format == "auto" else self.format)

    def create_parser(self) -> Parser[bytes, RTCMPacket]:
        """Creates an RTCM message parser for this preset."""
        return create_rtcm_parser(self.format)

    @property
    def json(self) -> Any:
        """Returns a JSON object representing this preset, in a format suitable
        for an RTK-INF message. Not all the fields are included, only the ones
        that are mandated by the RTK-INF message specification.
        """
        return {"title": self.title, "format": self.format, "sources": self.sources}


def create_filter_function(
    accept: Optional[Iterable[str]] = None, reject: Optional[Iterable[str]] = None
) -> Callable[[RTCMPacket], bool]:
    """Creates a filtering function that takes RTCM packets and returns whether
    the filter would accept the packet, based on a list of acceptable RTCM
    packet identifiers and a list of rejected RTCM packet identifiers.

    Each RTCM packet identifier consists of a prefix (``rtcm2/`` or ``rtcm3/``)
    and a numeric RTCM packet ID (e.g., ``rtcm3/1020`` identifies RTCMv3 packets
    of type 1020).

    Rejections are processed first, followed by the "accept" directives. A
    missing "accept" argument means that all packets are accepted (except the
    ones rejected explicitly). A missing "reject" argument also means that
    all packets are accepted by default.

    Parameters:
        accept: the list of RTCM packets to accept
        reject: the list of RTCM packets to reject

    Returns:
        an appropriate filter function
    """

    def _process_rtcm_packet_id_list(id_list):
        if id_list is None:
            return None

        result = {RTCMV2Packet: set(), RTCMV3Packet: set()}
        for spec in id_list:
            if spec.startswith("rtcm2/"):
                result[RTCMV2Packet].add(int(spec[6:]))
            elif spec.startswith("rtcm3/"):
                result[RTCMV3Packet].add(int(spec[6:]))

        return result

    if accept is None and reject is None:
        return constant(True)

    accept = _process_rtcm_packet_id_list(accept)
    reject = _process_rtcm_packet_id_list(reject)

    def filter(packet: RTCMPacket) -> bool:
        if isinstance(packet, RTCMV2Packet):
            cls = RTCMV2Packet
        elif isinstance(packet, RTCMV3Packet):
            cls = RTCMV3Packet
        else:
            return False

        if reject and packet.packet_type in reject[cls]:
            return False

        if accept and packet.packet_type not in accept[cls]:
            return False

        return True

    return filter
