from itertools import cycle
from logging import Logger
from typing import Iterable, Optional

from .types import MAVLinkMessageSpecification, spec

__all__ = ("RTKCorrectionPacketEncoder",)


class RTKCorrectionPacketEncoder:
    """Object whose responsibility is to encode a single RTCM3 RTK correction
    packet as one or more MAVLink `GPS_RTCM_DATA` messages, using fragmented
    packets if needed.
    """

    def __init__(self, log: Optional[Logger] = None):
        """Constructor."""
        self._log = log
        self._seq_no = cycle(range(32))

    def encode(self, packet: bytes) -> Iterable[MAVLinkMessageSpecification]:
        if len(packet) > 180:
            # fragmented packet
            slices = [packet[i : (i + 180)] for i in range(0, len(packet), 180)]
            slices[-1] = slices[-1].ljust(180, b"\x00")

            if len(slices) > 4:
                if self._log:
                    self._log.warn(
                        f"Dropping oversized RTCM packet: {len(packet)} bytes"
                    )
                return

            seq_no = next(self._seq_no) << 3

            for fragment_id, packet in enumerate(slices):
                flags = seq_no + (fragment_id << 1) + 1
                yield spec.gps_rtcm_data(
                    flags=flags, len=len(packet), data=packet.ljust(180, b"\x00")
                )

        else:
            # not fragmented packet
            yield spec.gps_rtcm_data(
                flags=0, len=len(packet), data=packet.ljust(180, b"\x00")
            )
