from typing import Optional

from .packets import ArtNetPacket

__all__ = ("ArtNetParser",)


ARTNET_MARKER = b"Art-Net\x00"


class ArtNetParser:
    """Simple ArtNet parser class."""

    def __call__(self, data: bytes) -> Optional[ArtNetPacket]:
        """Feeds the given bytes into the parser and returns the parsed ArtNet
        packet, or `None` if the incoming data does not represent a full ArtNet
        packet.
        """
        if data.startswith(ARTNET_MARKER):
            return ArtNetPacket.from_bytes(data)
        else:
            return None
