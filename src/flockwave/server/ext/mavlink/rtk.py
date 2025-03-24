from contextlib import contextmanager
from itertools import cycle
from logging import Logger
from typing import Callable, Iterable, Optional

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
        MAX_FRAGMENT_SIZE = 180

        if len(packet) > MAX_FRAGMENT_SIZE:
            # fragmented packet
            slices = [
                packet[i : (i + MAX_FRAGMENT_SIZE)]
                for i in range(0, len(packet), MAX_FRAGMENT_SIZE)
            ]

            if len(slices[-1]) == MAX_FRAGMENT_SIZE:
                # if the last fragment is full, we need to add an extra empty
                # one according to the protocol
                slices.append(b"")

            if len(slices) > 4:
                if self._log:
                    self._log.warning(
                        f"Dropping oversized RTCM packet: {len(packet)} bytes"
                    )
                return

            seq_no = next(self._seq_no)

            for fragment_id, packet in enumerate(slices):
                flags = (seq_no << 3) + (fragment_id << 1) + 1
                yield spec.gps_rtcm_data(
                    flags=flags, len=len(packet), data=packet.ljust(180, b"\x00")
                )

        else:
            # not fragmented packet
            yield spec.gps_rtcm_data(
                flags=0, len=len(packet), data=packet.ljust(180, b"\x00")
            )


class RTKCorrectionPacketSignalManager:
    """Object whose responsibility is to dispatch signals whenever an RTK
    correction packet is enqueued for transmission on the MAVLink networks
    managed by the extension.
    """

    _rtk_correction_packet_encoder: RTKCorrectionPacketEncoder
    """Encoder that is responsible for breaking up an RTK correction packet
    into individual MAVLink packets.
    """

    _log: Optional[Logger] = None
    """Logger to use for logging messages."""

    _sender: Optional[Callable[[Iterable[MAVLinkMessageSpecification]], None]]

    def __init__(self):
        """Constructor."""
        self._rtk_correction_packet_encoder = RTKCorrectionPacketEncoder()
        self._sender = None

    def _handle_packet(self, sender, packet: bytes):
        """Handles an RTK correction packet that the server wishes to forward
        to the drones in all the networks belonging to the extension.

        Parameters:
            packet: the raw RTK correction packet to forward to the drones in
                all the networks belonging to the extension
        """
        if not self._sender:
            return

        messages = list(self._rtk_correction_packet_encoder.encode(packet))
        if messages:
            try:
                self._sender(messages)
            except Exception:
                # We do not take responsibility for exceptions thrown in the
                # signal handlers
                if self._log:
                    self._log.exception(
                        "RTK packet fragment signal handler threw an exception"
                    )

    @contextmanager
    def use(self, signals, *, log):
        rtk_packet_fragments_signal = signals.get("mavlink:rtk_fragments")
        with signals.use({"rtk:packet": self._handle_packet}):
            if rtk_packet_fragments_signal:
                self._sender = lambda messages: rtk_packet_fragments_signal.send(
                    self, messages=messages
                )
            self._log = log
            try:
                yield
            finally:
                self._log = None
                self._sender = None
