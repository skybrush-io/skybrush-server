"""Flockwave server extension that adds support for reading SMPTE timecodes
from a MIDI connection.

Support for other connection types (e.g., TCP/IP) may be added later.
"""

from __future__ import division

from blinker import Signal
from collections import namedtuple
from eventlet import spawn
from eventlet.timeout import Timeout
from future.utils import python_2_unicode_compatible
from time import time

from .base import ExtensionBase

from ..model import ConnectionPurpose

from flockwave.server.model import StoppableClockBase
from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.connections.midi import MIDIPortConnection


_SMPTETimecodeBase = namedtuple(
    "SMPTETimecode", "hour minute second frame frames_per_second drop")


@python_2_unicode_compatible
class SMPTETimecode(_SMPTETimecodeBase):
    """Class representing a single SMPTE timecode.

    Attributes:
        hour (int): the hour part of the timecode
        minute (int): the minute part of the timecode
        second (int): the second part of the timecode
        frame (int): the frame part of the timecode
        frames_per_second (int): the number of frames per second
        drop (bool): whether this is a drop-frame timecode
    """

    @property
    def total_frames(self):
        """The total number of frames since the 00:00:00:00 timecode."""
        return ((self.hour * 60 + self.minute) * 60 + self.second) * \
            self.frames_per_second + self.frame

    @property
    def total_seconds(self):
        """The total number of seconds since the 00:00:00:00 timecode."""
        return self.hour * 3600 + self.minute * 60 + self.second + \
            self.frame / self.frames_per_second

    def __str__(self):
        return "{0.hour:02}:{0.minute:02}:{0.second:02}{1}{0.frame:02}"\
            .format(self, ";" if self.drop else ":")


class MIDITimecodeAssembler(object):
    """Stateful MIDI timecode assembler that receives MIDI messages with
    SysEx full time code messages and quarter-frame time code messages and
    yields the SMPTE timestamps parsed from these messages as well as the
    corresponding timestamps according to the local system clock.
    """

    @classmethod
    def stream(cls, messages):
        """Shorthand notation for taking an iterable yielding MIDI timecode
        messages and yielding SMPTE timecodes from them.

        Parameters:
            message (iterable[mido.Message]): an iterable yielding MIDI
                messages. Messages that do not correspond to MIDI timecodes
                are ignored.

        Yields:
            (int, SMPTETimecode, bool): a tuple consisting of the timestamp
                when the first byte of the SMPTE timecode was received, the
                SMPTE timecode itself, and whether the clock is assumed to
                be running now, for each timecode parsed from the inbound
                messages
        """
        assembler = cls()
        for message in messages:
            result = assembler.feed(message)
            if result is not None:
                yield result

    def __init__(self):
        """Constructor."""
        self.reset()

    def feed(self, message):
        """Feeds a single MIDI message into the timecode assembler.

        Returns:
            Optional[(int, SMPTETimecode, bool)]: a tuple consisting of the
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

    def reset(self):
        """Resets the state of the assembler."""
        self._expected_frame_type = 0
        self._frame, self._second, self._minute, self._hour = 0, 0, 0, 0
        self._time = None

    def _feed_quarter_frame(self, message):
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
            frames_per_second, is_drop_frame = self._rate_bits_to_fps(
                (value & 6) >> 1)
            timecode = SMPTETimecode(hour=self._hour, minute=self._minute,
                                     second=self._second, frame=self._frame,
                                     frames_per_second=frames_per_second,
                                     drop=is_drop_frame)
            result = self._time, timecode, True

        self._expected_frame_type = (self._expected_frame_type + 1) % 8
        return result

    def _feed_sysex_frame(self, message):
        if message.data[0:4] != (127, 127, 1, 1):
            # Not a full MIDI timecode frame
            return None

        now = time()
        hour, minute, second, frame = message.data[4:8]
        rate_bits = (hour >> 5) & 3
        hour = hour & 31
        frames_per_second, is_drop_frame = self._rate_bits_to_fps(rate_bits)
        timecode = SMPTETimecode(hour=hour, minute=minute, second=second,
                                 frame=frame, drop=is_drop_frame,
                                 frames_per_second=frames_per_second)
        return now, timecode, False

    @staticmethod
    def _rate_bits_to_fps(value):
        """Given a data byte from a MIDI timecode quarter frame of type 7 or
        a full MIDI timecode frame, returns the frame rate and whether it is
        a drop-frame MIDI timecode.

        Parameters:
            value (int): the frame rate bits of a MIDI timecode quarter
                frame of type 7, or of a full MIDI timecode frame. The bits
                have to be shifted down to the least significant positions
                before calling this function; in other words, the only
                allowed values here are 0, 1, 2 or 3.

        Returns:
            (int, bool): the number of frames per second and whether this is
                a drop-frame MIDI timecode
        """
        rate_bits = value & 3
        frames_per_second = (24, 25, 30, 30)[rate_bits]
        return frames_per_second, rate_bits == 2


class MIDIClock(StoppableClockBase):
    """Clock subclass that the extension provides and registers.

    Attributes:
        last_timecode (SMPTETimecode): the last SMPTE timecode received from
            the MIDI port
    """

    def __init__(self):
        """Constructor."""
        super(MIDIClock, self).__init__(id="mtc")
        self._last_timecode = None
        self._last_local_timestamp = None

    def _calculate_drift(self, timecode, local_timestamp):
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
        if self._last_timecode is None:
            return 0.0

        delta_timecode = timecode.total_frames - \
            self._last_timecode.total_frames

        if self.running:
            delta_local_time = local_timestamp - self._last_local_timestamp
            delta_local_time *= self.ticks_per_second
            return delta_timecode - delta_local_time
        else:
            return delta_timecode

    def notify_timecode(self, timecode, local_timestamp):
        """Notify the clock that an SMPTE timecode was observed on the MIDI
        connection.

        Parameters:
            timecode (SMPTETimecode): the timecode that was observed
            local_timestamp (float): the local timestamp (in UTC) when the
                timecode was observed
        """
        drift = self._calculate_drift(timecode, local_timestamp)

        self._last_timecode = timecode
        self._last_local_timestamp = local_timestamp
        self.ticks_per_second = timecode.frames_per_second

        if abs(drift) > 2:
            self.changed.send(self, delta=drift)

    def ticks_given_time(self, now):
        """Returns the number of frames elapsed since the epoch of the
        clock, assuming that the internal clock of the server has a given
        value.

        Parameters:
            now (float): the state of the internal clock of the server,
                expressed in number of seconds since the Unix epoch

        Returns:
            float: the number of frames elapsed
        """
        if self._last_timecode is None:
            return 0.0

        # If the clock is running, we have to extrapolate from the last
        # timecode and local timestamp to get the correct number of ticks.
        # If the clock is not running, we just return the last timecode.
        if not self.running:
            return self._last_timecode.total_frames
        else:
            elapsed = now - self._last_local_timestamp
            return self._last_timecode.total_frames + \
                elapsed * self._last_timecode.frames_per_second


class SMPTETimecodeExtension(ExtensionBase):
    """Extension that adds support for reading SMPTE timecode from a
    connection.
    """

    def __init__(self, *args, **kwds):
        """Constructor."""
        super(SMPTETimecodeExtension, self).__init__(*args, **kwds)
        self._clock = None
        self._inbound_parser = None
        self._inbound_thread = None
        self._midi = None

    @property
    def clock(self):
        """The clock that the extension provides and registers in the
        server.
        """
        return self._clock

    def configure(self, configuration):
        """Configures the extension."""
        self.midi = create_connection(configuration.get("connection"))
        if self.midi is None:
            # This can happen if there is no MIDI support on the current
            # platform
            pass
        elif not isinstance(self.midi, MIDIPortConnection):
            raise TypeError("{0} supports MIDIPortConnection connections "
                            "only".format(self.__class__.__name__))
        else:
            self.midi = reconnecting(self.midi)

    @property
    def midi(self):
        """The MIDI connection that the thread reads messages from."""
        return self._midi

    @midi.setter
    def midi(self, value):
        if self._midi == value:
            return

        if self._midi is not None:
            self._inbound_parser.timecode_received.disconnect(
                self._on_timecode_received
            )
            self._inbound_thread.kill()
            self._inbound_parser = None
            self._inbound_thread = None
            self._midi.close()

            self.app.connection_registry.remove("MIDI")

        self._midi = value

        if self._midi is not None:
            try:
                self._midi.open()
            except ImportError as ex:
                self.log.warn("No MIDI support; {0.name!r} module is missing".format(ex))
                self._midi = None
                return

            self.app.connection_registry.add(
                self._midi, "MIDI",
                description="MIDI timecode provider",
                purpose=ConnectionPurpose.time
            )

            self._set_clock(MIDIClock())

            self._inbound_parser = InboundMessageParserThread(self._midi)
            self._inbound_parser.timecode_received.connect(
                self._on_timecode_received, sender=self._inbound_parser
            )

            self._inbound_thread = spawn(self._inbound_parser.run)
        else:
            self._set_clock(None)

    def _on_clock_changed(self, sender, delta):
        """Handler called when the MIDI clock was adjusted."""
        self.log.warn("MIDI clock adjusted by {0} frame(s)".format(delta))

    def _on_clock_started(self, sender):
        """Handler called when the MIDI clock was started."""
        self.log.info("MIDI clock started", extra={"id": self._clock.id})

    def _on_clock_stopped(self, sender):
        """Handler called when the MIDI clock was stopped."""
        self.log.info("MIDI clock stopped", extra={"id": self._clock.id})

    def _on_timecode_received(self, sender, timecode, local_timestamp,
                              running):
        """Handler called when a new timecode was received by the
        inbound thread.

        Parameters:
            sender (InboundMessageParserThread): the parser that received
                the timecode
            timecode (Optional[SMPTETimecode]): the timecode that was
                received or ``None`` if no timecode was received and we
                only need to update whether the clock is running or not
            local_timestamp (float): the local timestamp corresponding to
                the timecode, in UTC
            running (bool): whether the clock is running
        """
        if timecode is not None:
            self._clock.notify_timecode(timecode, local_timestamp)
        self._clock.running = running

    def _set_clock(self, value):
        """Private setter for the ``clock`` property that should not be
        called from the outside.
        """
        if value == self._clock:
            return

        if self._clock is not None:
            self._clock.stop()
            self._clock.changed.disconnect(self._on_clock_changed)
            self._clock.started.disconnect(self._on_clock_started)
            self._clock.stopped.disconnect(self._on_clock_stopped)
            self.app.clock_registry.remove(self._clock)

        self._clock = value

        if self._clock is not None:
            self.app.clock_registry.add(self._clock)
            self._clock.changed.connect(self._on_clock_changed,
                                        sender=self._clock)
            self._clock.started.connect(self._on_clock_started,
                                        sender=self._clock)
            self._clock.stopped.connect(self._on_clock_stopped,
                                        sender=self._clock)


class InboundMessageParserThread(object):
    """Green thread that parses and processes inbound MIDI messages.

    Attributes:
        port (MIDIConnection): the MIDI connection that the thread reads
            messages from.
    """

    timecode_received = Signal()

    def __init__(self, port):
        """Constructor.

        Parameters:
            port (MIDIConnection): the MIDI connection to read messages from
        """
        self.port = port
        self.assembler = MIDITimecodeAssembler()

    def _read_next_timecode(self):
        """Reads the next timecode frame from the MIDI connection. Blocks
        until the next timecode is received.
        """
        while True:
            result = self.assembler.feed(self.port.read())
            if result is not None:
                return result

    def run(self):
        """Body of the green thread that reads messages in an infinite loop
        from the MIDI connection.
        """
        running = False
        while True:
            result = None
            if running:
                # Wait for the next MIDI timecode frame with a timeout. If
                # no timecode frame arrives within 0.2 seconds, stop the
                # clock. In theory, 0.1 seconds should be plenty, but
                # there seems to be large delays when using Ardour as the
                # MTC master on Linux and sometimes 0.1 seconds is not enough.
                with Timeout(0.2, False):
                    result = self._read_next_timecode()
            else:
                # Just read the next timecode without a timeout.
                result = self._read_next_timecode()

            if result is None:
                # The clock was playing and we have timed out. Stop the
                # clock without updating the timecode.
                timestamp, timecode, running = None, None, False
            else:
                # Update the timecode.
                timestamp, timecode, running = result

            self.timecode_received.send(self, timecode=timecode,
                                        local_timestamp=timestamp,
                                        running=running)


construct = SMPTETimecodeExtension
