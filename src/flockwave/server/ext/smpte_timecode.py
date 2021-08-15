"""Skybrush server extension that adds support for reading SMPTE timecodes
from a MIDI connection.

Support for other connection types (e.g., TCP/IP) may be added later.
"""

from __future__ import division

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from logging import Logger
from time import time
from trio import move_on_after
from typing import AsyncIterator, Iterator, Optional, Tuple

from .base import ExtensionBase

from ..model import ConnectionPurpose

from flockwave.server.model import StoppableClockBase
from flockwave.connections import create_connection
from flockwave.connections.midi import MIDIPortConnection


@dataclass
class SMPTETimecode:
    """Class representing a single SMPTE timecode.

    Attributes:
        hour: the hour part of the timecode
        minute: the minute part of the timecode
        second: the second part of the timecode
        frame: the frame part of the timecode
        frames_per_second: the number of frames per second
        drop: whether this is a drop-frame timecode
    """

    hour: int = 0
    minute: int = 0
    second: int = 0
    frame: int = 0
    frames_per_second: int = 25
    drop: bool = False

    @property
    def total_frames(self) -> int:
        """The total number of frames since the 00:00:00:00 timecode."""
        return (
            (self.hour * 60 + self.minute) * 60 + self.second
        ) * self.frames_per_second + self.frame

    @property
    def total_seconds(self) -> float:
        """The total number of seconds since the 00:00:00:00 timecode."""
        return (
            self.hour * 3600
            + self.minute * 60
            + self.second
            + self.frame / self.frames_per_second
        )

    def __str__(self):
        return "{0.hour:02}:{0.minute:02}:{0.second:02}{1}{0.frame:02}".format(
            self, ";" if self.drop else ":"
        )


class MIDITimecodeAssembler:
    """Stateful MIDI timecode assembler that receives MIDI messages with
    SysEx full time code messages and quarter-frame time code messages and
    yields the SMPTE timestamps parsed from these messages as well as the
    corresponding timestamps according to the local system clock.
    """

    _expected_frame_type: int
    _frame: int
    _hour: int
    _minute: int
    _second: int
    _time: Optional[float]

    @classmethod
    def stream(cls, messages) -> Iterator[Tuple[float, SMPTETimecode, bool]]:
        """Shorthand notation for taking an iterable yielding MIDI timecode
        messages and yielding SMPTE timecodes from them.

        Parameters:
            message (iterable[mido.Message]): an iterable yielding MIDI
                messages. Messages that do not correspond to MIDI timecodes
                are ignored.

        Yields:
            a tuple consisting of the timestamp when the first byte of the SMPTE
            timecode was received, the SMPTE timecode itself, and whether the
            clock is assumed to be running now, for each timecode parsed from
            the inbound messages
        """
        assembler = cls()
        for message in messages:
            result = assembler.feed(message)
            if result is not None:
                yield result

    def __init__(self):
        """Constructor."""
        self.reset()

    def feed(self, message) -> Optional[Tuple[float, SMPTETimecode, bool]]:
        """Feeds a single MIDI message into the timecode assembler.

        Returns:
            Optional[(float, SMPTETimecode, bool)]: a tuple consisting of the
                timestamp when the first byte of the SMPTE timecode was
                received, the SMPTE timecode itself, and whether the clock
                is assumed to be running now, or ``None`` if the
                frames fed into the assembler did not provide enough
                information to yield a timecode yet
        """
        if message.type == "quarter_frame":
            return self._feed_quarter_frame(message)
        elif message.type == "sysex":
            return self._feed_sysex_frame(message)
        else:
            return None

    def reset(self) -> None:
        """Resets the state of the assembler."""
        self._expected_frame_type = 0
        self._frame, self._second, self._minute, self._hour = 0, 0, 0, 0
        self._time = None

    def _feed_quarter_frame(
        self, message
    ) -> Optional[Tuple[float, SMPTETimecode, bool]]:
        frame_type = message.frame_type
        result = None

        if frame_type != self._expected_frame_type:
            return None

        value = message.frame_value
        if frame_type == 0:
            self._time = time()
            self._frame = value
        elif frame_type == 1:
            self._frame += value << 4
        elif frame_type == 2:
            self._second = value
        elif frame_type == 3:
            self._second += value << 4
        elif frame_type == 4:
            self._minute = value
        elif frame_type == 5:
            self._minute += value << 4
        elif frame_type == 6:
            self._hour = value
        elif frame_type == 7:
            self._hour += (value & 1) << 4
            frames_per_second, is_drop_frame = self._rate_bits_to_fps((value & 6) >> 1)
            timecode = SMPTETimecode(
                hour=self._hour,
                minute=self._minute,
                second=self._second,
                frame=self._frame,
                frames_per_second=frames_per_second,
                drop=is_drop_frame,
            )
            if self._time is not None:
                result = self._time, timecode, True
            else:
                result = None

        self._expected_frame_type = (self._expected_frame_type + 1) % 8
        return result

    def _feed_sysex_frame(self, message) -> Optional[Tuple[float, SMPTETimecode, bool]]:
        if message.data[0:4] != (127, 127, 1, 1):
            # Not a full MIDI timecode frame
            return None

        now = time()
        hour, minute, second, frame = message.data[4:8]
        rate_bits = (hour >> 5) & 3
        hour = hour & 31
        frames_per_second, is_drop_frame = self._rate_bits_to_fps(rate_bits)
        timecode = SMPTETimecode(
            hour=hour,
            minute=minute,
            second=second,
            frame=frame,
            drop=is_drop_frame,
            frames_per_second=frames_per_second,
        )
        return now, timecode, False

    @staticmethod
    def _rate_bits_to_fps(value: int) -> Tuple[int, bool]:
        """Given a data byte from a MIDI timecode quarter frame of type 7 or
        a full MIDI timecode frame, returns the frame rate and whether it is
        a drop-frame MIDI timecode.

        Parameters:
            value: the frame rate bits of a MIDI timecode quarter frame of type
                7, or of a full MIDI timecode frame. The bits have to be shifted
                down to the least significant positions before calling this
                function; in other words, the only allowed values here are 0, 1,
                2 or 3.

        Returns:
            the number of frames per second and whether this is a drop-frame
            MIDI timecode
        """
        rate_bits = value & 3
        frames_per_second = (24, 25, 30, 30)[rate_bits]
        return frames_per_second, rate_bits == 2


class MIDIClock(StoppableClockBase):
    """Clock subclass that the extension provides and registers."""

    #: The last SMPTE timecode received from the MIDI port
    _last_timecode: SMPTETimecode

    #: The local timestamp when the last SMPTE timecode was received
    _last_local_timestamp: float

    def __init__(self):
        """Constructor."""
        super().__init__(id="mtc")
        self._last_timecode = SMPTETimecode()
        self._last_local_timestamp = 0

    def _calculate_drift(
        self, timecode: SMPTETimecode, local_timestamp: float
    ) -> float:
        """Calculates how much the MIDI clock has drifted, based on the
        last timecode, the local timestamp when the last timecode was
        received (stored in the instance attributes), a newly received
        timecode and the local timestamp that belongs to the newly received
        timecode.

        If the clock is not playing, the drift will simply be the difference
        between the two timecodes, measured in frames. If the clock is
        playing, the drift will be the difference between the timecode
        difference (in frames) and the timestamp difference (also in
        frames).

        Returns:
            float: the drift of the MIDI clock from the local clock. Should
                be less than the duration of a single frame in most cases
                when the clock is running.
        """
        delta_timecode = timecode.total_frames - self._last_timecode.total_frames

        if self.running:
            delta_local_time = local_timestamp - self._last_local_timestamp
            delta_local_time *= self.ticks_per_second
            return delta_timecode - delta_local_time
        else:
            return delta_timecode

    def notify_timecode(self, timecode: SMPTETimecode, local_timestamp: float) -> None:
        """Notify the clock that an SMPTE timecode was observed on the MIDI
        connection.

        Parameters:
            timecode: the timecode that was observed
            local_timestamp: the local timestamp (in UTC) when the timecode was
                observed
        """
        drift = self._calculate_drift(timecode, local_timestamp)

        self._last_timecode = timecode
        self._last_local_timestamp = local_timestamp
        self.ticks_per_second = timecode.frames_per_second

        if abs(drift) > 2:
            self.changed.send(self, delta=drift)

    def ticks_given_time(self, now: float) -> float:
        """Returns the number of frames elapsed since the epoch of the
        clock, assuming that the internal clock of the server has a given
        value.

        Parameters:
            now: the state of the internal clock of the server, expressed in
                number of seconds since the Unix epoch

        Returns:
            the number of frames elapsed
        """
        # If the clock is running, we have to extrapolate from the last
        # timecode and local timestamp to get the correct number of ticks.
        # If the clock is not running, we just return the last timecode.
        elapsed = now - self._last_local_timestamp if self.running else 0.0
        return (
            self._last_timecode.total_frames
            + elapsed * self._last_timecode.frames_per_second
        )


class InboundMessageParser:
    """Trio task that parses and processes inbound MIDI messages."""

    #: Assembler object used to parse a timecode from consecutive MIDI messages
    assembler: MIDITimecodeAssembler

    #: The MIDI connection that the parser reads messages from
    port: MIDIPortConnection

    @dataclass
    class Message:
        timecode: Optional[SMPTETimecode] = None
        local_timestamp: Optional[float] = None
        running: bool = False

    def __init__(self, port: MIDIPortConnection):
        """Constructor.

        Parameters:
            port (MIDIConnection): the MIDI connection to read messages from
        """
        self.port = port
        self.assembler = MIDITimecodeAssembler()

    async def _read_next_timecode(self) -> Tuple[float, SMPTETimecode, bool]:
        """Reads the next timecode frame from the MIDI connection. Blocks
        until the next timecode is received.
        """
        while True:
            message = await self.port.read()
            result = self.assembler.feed(message)
            if result is not None:
                return result

    async def run(self) -> AsyncIterator[Message]:
        """Main entry point of the Trio task that reads messages in an infinite
        loop from the MIDI connection.
        """
        running = False
        await self.port.wait_until_connected()
        while True:
            result = None
            if running:
                # Wait for the next MIDI timecode frame with a timeout. If
                # no timecode frame arrives within 0.2 seconds, stop the
                # clock. In theory, 0.1 seconds should be plenty, but
                # there seems to be large delays when using Ardour as the
                # MTC master on Linux and sometimes 0.1 seconds is not enough.
                with move_on_after(0.2):
                    result = await self._read_next_timecode()
            else:
                # Just read the next timecode without a timeout.
                result = await self._read_next_timecode()

            if result is None:
                # The clock was playing and we have timed out. Stop the
                # clock without updating the timecode.
                running = False
                yield self.Message()
            else:
                # Update the timecode.
                timestamp, timecode, running = result
                yield self.Message(
                    local_timestamp=timestamp, timecode=timecode, running=running
                )


class SMPTETimecodeExtension(ExtensionBase):
    """Extension that adds support for reading SMPTE timecode from a
    connection.
    """

    log: Logger

    _clock: Optional[MIDIClock]

    def __init__(self, *args, **kwds):
        """Constructor."""
        super().__init__(*args, **kwds)
        self._clock = None

    @property
    def clock(self) -> Optional[MIDIClock]:
        """The clock that the extension provides and registers in the
        server.
        """
        return self._clock

    async def run(self, app, configuration, logger):
        conn = configuration.get("connection")
        conn = create_connection(conn) if conn else None

        if conn is None:
            # This can happen if there is no MIDI support on the current
            # platform
            return
        elif not isinstance(conn, MIDIPortConnection):
            raise TypeError(
                f"{self.__class__.__name__} supports MIDIPortConnection "
                "connections only"
            )

        with ExitStack() as stack:
            stack.enter_context(
                app.connection_registry.use(
                    conn,
                    "MIDI",
                    description="MIDI timecode provider",
                    purpose=ConnectionPurpose.time,  # type: ignore
                )
            )
            stack.enter_context(self._use_clock(MIDIClock()))
            await app.supervise(conn, task=self._handle_midi_messages)

    async def _handle_midi_messages(self, conn: MIDIPortConnection) -> None:
        task = InboundMessageParser(conn)
        async for message in task.run():
            self._on_timecode_received(message)

    def _on_clock_changed(self, sender, delta: float) -> None:
        """Handler called when the MIDI clock was adjusted."""
        self.log.warn("MIDI clock adjusted by {0} frame(s)".format(delta))

    def _on_clock_started(self, sender) -> None:
        """Handler called when the MIDI clock was started."""
        assert self._clock is not None
        self.log.info("MIDI clock started", extra={"id": self._clock.id})

    def _on_clock_stopped(self, sender) -> None:
        """Handler called when the MIDI clock was stopped."""
        assert self._clock is not None
        self.log.info("MIDI clock stopped", extra={"id": self._clock.id})

    def _on_timecode_received(self, message: InboundMessageParser.Message) -> None:
        """Handler called when a new timecode was received by the
        inbound thread.

        Parameters:
            message: the timecode that was received, and the corresponding local
                timestamp
        """
        if not self._clock:
            return

        if message.timecode is not None:
            assert message.local_timestamp is not None
            self._clock.notify_timecode(message.timecode, message.local_timestamp)

        self._clock.running = message.running

    def _set_clock(self, value: Optional[MIDIClock]) -> None:
        """Private setter for the ``clock`` property that should not be
        called from the outside.
        """
        if value == self._clock:
            return

        assert self.app is not None

        if self._clock is not None:
            self._clock.stop()
            self._clock.changed.disconnect(self._on_clock_changed)
            self._clock.started.disconnect(self._on_clock_started)
            self._clock.stopped.disconnect(self._on_clock_stopped)
            self.app.import_api("clocks").unregister_clock(self._clock)

        self._clock = value

        if self._clock is not None:
            self.app.import_api("clocks").register_clock(self._clock)
            self._clock.changed.connect(self._on_clock_changed, sender=self._clock)
            self._clock.started.connect(self._on_clock_started, sender=self._clock)
            self._clock.stopped.connect(self._on_clock_stopped, sender=self._clock)

    @contextmanager
    def _use_clock(self, value: MIDIClock) -> Iterator[None]:
        """Context manager variant of ``self._set_clock()``."""
        old_clock = self.clock
        self._set_clock(value)
        try:
            yield
        finally:
            self._set_clock(old_clock)


construct = SMPTETimecodeExtension
dependencies = ("clocks",)
schema = {
    "properties": {
        "connection": {
            "type": "string",
            "title": "Connection URL",
            "description": (
                "Connection URL to the MIDI timecode device; must start with "
                '"midi:". Example: "midi:IAC Driver Bus 1"',
            ),
        }
    }
}
