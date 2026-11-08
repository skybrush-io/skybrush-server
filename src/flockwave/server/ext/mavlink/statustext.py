"""Handling of MAVLink STATUSTEXT messages, including the reassembly of
messages that were split into multiple chunks by the sender.

MAVLink STATUSTEXT messages can carry at most 50 characters. Longer texts are
split into chunks that share the same nonzero ``id`` and are numbered with
``chunk_seq``. The last chunk is the one that is shorter than 50 characters.
When the length of the text is a multiple of 50, the sender adds an extra chunk
that contains null characters only.
A message with ``id`` set to zero always fits in a single chunk.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

from trio_util import periodic

from .types import MAVLinkMessage, MAVLinkStatusTextTargetSpecification
from .utils import (
    flockwave_severity_from_mavlink_severity,
    log_id_for_uav,
    log_id_from_message,
    python_log_level_from_mavlink_severity,
)

if TYPE_CHECKING:
    from .driver import MAVLinkUAV
    from .network import MAVLinkNetwork

__all__ = ("StatusText", "StatusTextAssembler", "StatusTextManager")

MAX_CHUNK_LENGTH = 50
"""Maximum number of characters in the text of a single STATUSTEXT chunk."""

MISSING_CHUNK_MARKER = "[...]"
"""Marker inserted into the reassembled text where chunks are missing."""


@dataclass(frozen=True)
class StatusText:
    """A complete (or partially reassembled) STATUSTEXT message."""

    text: str
    """The text of the message."""

    severity: int
    """The MAVLink severity of the message."""

    message: MAVLinkMessage
    """The first chunk of the message that was received. Useful for
    identifying the sender of the message.
    """

    uav: MAVLinkUAV | None
    """The UAV that sent the message; ``None`` if the sender is unknown."""


@dataclass
class _PartialStatusText:
    """Chunks of a STATUSTEXT message that is being reassembled."""

    severity: int
    message: MAVLinkMessage
    uav: MAVLinkUAV | None
    started_at: float
    chunks: dict[int, str] = field(default_factory=dict)
    last_seq: int | None = None
    """Sequence number of the final chunk; ``None`` if it has not arrived yet."""

    @property
    def is_complete(self) -> bool:
        return self.last_seq is not None and len(self.chunks) == self.last_seq + 1

    def assemble(self) -> StatusText:
        """Joins the chunks received so far.

        Each run of missing chunks is replaced by a single marker. The marker
        is also appended when the final chunk has not arrived.
        """
        last_seq = self.last_seq if self.last_seq is not None else max(self.chunks)
        parts: list[str] = []
        in_gap = False
        for seq in range(last_seq + 1):
            chunk = self.chunks.get(seq)
            if chunk is not None:
                parts.append(chunk)
                in_gap = False
            elif not in_gap:
                parts.append(MISSING_CHUNK_MARKER)
                in_gap = True

        if self.last_seq is None:
            parts.append(MISSING_CHUNK_MARKER)

        return StatusText(
            text="".join(parts),
            severity=self.severity,
            message=self.message,
            uav=self.uav,
        )


class StatusTextAssembler:
    """Reassembles STATUSTEXT messages that were split into multiple chunks.

    Incomplete messages are kept for a limited time. Call `flush_expired()`
    regularly to retrieve the messages whose remaining chunks did not arrive
    in time.
    """

    _clock: Callable[[], float]
    _partials: dict[tuple[int, int, int], _PartialStatusText]
    _timeout: float

    def __init__(self, timeout: float = 1.0, clock: Callable[[], float] = monotonic):
        """Constructor.

        Args:
            timeout: number of seconds to wait for the missing chunks of a
                message after its first chunk arrived
            clock: function that returns the current time in seconds
        """
        self._clock = clock
        self._partials = {}
        self._timeout = timeout

    def add(
        self, message: MAVLinkMessage, uav: MAVLinkUAV | None = None
    ) -> Sequence[StatusText]:
        """Processes a single STATUSTEXT message.

        Args:
            message: the STATUSTEXT message
            uav: the UAV that sent the message, if known

        Returns:
            the messages that became ready for processing. It is empty if
            the message is a chunk of a longer message that is not complete
            yet. It may contain more than one item if the message forced an
            earlier, incomplete message to be flushed.
        """
        text: str = message.text or ""
        msg_id: int = getattr(message, "id", 0) or 0
        if not msg_id:
            return [StatusText(text, message.severity, message, uav)]

        seq: int = getattr(message, "chunk_seq", 0) or 0
        key = (message.get_srcSystem(), message.get_srcComponent(), msg_id)
        result: list[StatusText] = []

        partial = self._partials.get(key)
        if partial is not None:
            if partial.chunks.get(seq) == text and partial.severity == message.severity:
                # Duplicate chunk, e.g., received over a redundant link
                return result

            if (
                seq in partial.chunks
                or partial.severity != message.severity
                or (partial.last_seq is not None and seq > partial.last_seq)
            ):
                # The sender reused the ID for a new message
                result.append(partial.assemble())
                partial = None

        if partial is None:
            partial = _PartialStatusText(
                severity=message.severity,
                message=message,
                uav=uav,
                started_at=self._clock(),
            )
            self._partials[key] = partial

        partial.chunks[seq] = text
        if len(text) < MAX_CHUNK_LENGTH:
            partial.last_seq = seq

        if partial.is_complete:
            del self._partials[key]
            result.append(partial.assemble())

        return result

    def flush_expired(self) -> Sequence[StatusText]:
        """Returns the incomplete messages whose timeout has expired and
        removes them from the assembler.
        """
        deadline = self._clock() - self._timeout
        expired = [
            key
            for key, partial in self._partials.items()
            if partial.started_at <= deadline
        ]
        return [self._partials.pop(key).assemble() for key in expired]


class StatusTextManager:
    """Class that handles the MAVLink STATUSTEXT messages received on a single
    MAVLink network.

    Chunked messages are reassembled first. Complete messages are checked for
    prearm errors, and then forwarded to the server log or to the GCS
    depending on their severity.
    """

    _assembler: StatusTextAssembler
    """Object that reassembles chunked STATUSTEXT messages."""

    _network: MAVLinkNetwork
    """The MAVLink network that owns this manager."""

    _targets: MAVLinkStatusTextTargetSpecification
    """Object specifying how STATUSTEXT messages should be handled."""

    def __init__(
        self,
        network: MAVLinkNetwork,
        targets: MAVLinkStatusTextTargetSpecification = MAVLinkStatusTextTargetSpecification.DEFAULT,
        *,
        timeout: float = 1.0,
    ):
        """Constructor.

        Args:
            network: the network whose STATUSTEXT messages this object manages
            targets: specifies where to forward the STATUSTEXT messages
            timeout: number of seconds to wait for the missing chunks of a
                chunked message after its first chunk arrived
        """
        self._assembler = StatusTextAssembler(timeout=timeout)
        self._network = network
        self._targets = targets

    def handle_message(self, message: MAVLinkMessage, uav: MAVLinkUAV | None) -> None:
        """Handles an incoming MAVLink STATUSTEXT message. Chunked messages
        are reassembled before processing.

        Args:
            message: the STATUSTEXT message
            uav: the UAV that sent the message; ``None`` if the sender is
                unknown
        """
        for status_text in self._assembler.add(message, uav):
            self._process(status_text)

    async def run(self) -> None:
        """Background task that processes the chunked STATUSTEXT messages
        whose remaining chunks did not arrive in time.
        """
        async for _ in periodic(0.5):
            for status_text in self._assembler.flush_expired():
                self._process(status_text)

    def _process(self, status_text: StatusText) -> None:
        """Processes a complete (or partially reassembled) STATUSTEXT message
        and forwards it to the log console or to the GCS, depending on how the
        network is set up.
        """
        text = status_text.text
        if not text:
            return

        message = status_text.message
        uav = status_text.uav
        if uav and uav._autopilot.is_prearm_error_message(text):
            uav.notify_prearm_failure(uav._autopilot.process_prearm_error_message(text))
            if not self._targets.log_prearm:
                return

        severity = status_text.severity
        log = self._network.log
        if severity <= self._targets.server and log:
            log_id = (
                log_id_for_uav(uav)
                if uav
                else log_id_from_message(message, self._network.id)
            )
            log.log(
                python_log_level_from_mavlink_severity(severity),
                text,
                extra={"id": log_id, "telemetry": "ignore"},
            )
        if severity <= self._targets.client and uav is not None:
            uav.send_log_message_to_gcs(
                text,
                severity=flockwave_severity_from_mavlink_severity(severity),
            )
