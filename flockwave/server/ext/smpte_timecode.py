"""Flockwave server extension that adds support for reading SMPTE timecodes
from a MIDI connection.

Support for other connection types (e.g., TCP/IP) may be added later.
"""

from collections import namedtuple
from eventlet import spawn
from time import time

from .base import ExtensionBase
from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.connections.midi import MIDIPortConnection


_SMPTETimecodeBase = namedtuple(
    "SMPTETimecode", "hour minute second frame frames_per_second drop")


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
            (int, SMPTETimecode): a tuple consisting of the timestamp when the
                first byte of the SMPTE timecode was received, and the SMPTE
                timecode itself, for each timecode parsed from the inbound
                messages
        """
        assembler = cls()
        for message in messages:
            timecode = assembler.feed(message)
            if timecode is not None:
                yield timecode

    def __init__(self):
        """Constructor."""
        self.reset()

    def feed(self, message):
        """Feeds a single MIDI message into the timecode assembler.

        Returns:
            Optional[(int, SMPTETimecode)]: a tuple consisting of the
                timestamp when the first byte of the SMPTE timecode was
                received, and the SMPTE timecode itself, or ``None`` if the
                frames fed into the assembler did not provide enough
                information to yield a timecode yet
        """
        if message.type == "quarter_frame":
            return self._feed_quarter_frame(message)
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
            frames_per_second, is_drop_frame = self._rate_bits_to_fps(value)
            timecode = SMPTETimecode(hour=self._hour, minute=self._minute,
                                     second=self._second, frame=self._frame,
                                     frames_per_second=frames_per_second,
                                     drop=is_drop_frame)
            result = self._time, timecode

        self._expected_frame_type = (self._expected_frame_type + 1) % 8
        return result

    @staticmethod
    def _rate_bits_to_fps(value):
        """Given a data byte from a MIDI timecode quarter frame of type 7,
        returns the frame rate and whether it is a drop-frame MIDI timecode.

        Parameters:
            value (int): the data byte of a MIDI timecode quarter frame of
                type 7

        Returns:
            (int, bool): the number of frames per second and whether this is
                a drop-frame MIDI timecode
        """
        rate_bits = (value & 6) >> 1
        frames_per_second = (24, 25, 30, 30)[rate_bits]
        return frames_per_second, rate_bits == 2


class SMPTETimecodeExtension(ExtensionBase):
    """Extension that adds support for reading SMPTE timecode from a
    connection.

    Attributes:
        midi (MIDIConnection): a connection object that yields MIDI messages
    """

    def __init__(self, *args, **kwds):
        """Constructor."""
        super(SMPTETimecodeExtension, self).__init__(*args, **kwds)
        self._inbound_thread = None
        self._midi = None

    def configure(self, configuration):
        """Configures the extension."""
        self.midi = create_connection(configuration.get("connection"))
        if not isinstance(self.midi, MIDIPortConnection):
            raise TypeError("{0} supports MIDIPortConnection connections "
                            "only".format(self.__class__.__name__))
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
            self._inbound_thread.kill()
            self._inbound_thread = None
            self._midi.close()

        self._midi = value

        if self._midi is not None:
            self._midi.open()
            self._inbound_thread = spawn(
                InboundMessageParserThread(self._midi).run
            )


class InboundMessageParserThread(object):
    """Green thread that parses and processes inbound MIDI messages.

    Attributes:
        port (MIDIConnection): the MIDI connection that the thread reads
            messages from.
    """

    def __init__(self, port):
        """Constructor.

        Parameters:
            port (MIDIConnection): the MIDI connection to read messages from
        """
        self.port = port

    def run(self):
        """Body of the green thread that reads messages in an infinite loop
        from the MIDI connection.
        """
        assembler = MIDITimecodeAssembler()
        while True:
            result = assembler.feed(self.port.read())
            if result is not None:
                local_timestamp, timecode = result
                # TODO: emit a signal here
                print(timecode)


construct = SMPTETimecodeExtension
