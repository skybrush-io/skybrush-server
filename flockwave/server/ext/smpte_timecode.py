"""Flockwave server extension that adds support for reading SMPTE timecodes
from a MIDI connection.

Support for other connection types (e.g., TCP/IP) may be added later.
"""

from .base import ExtensionBase
from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.connections.midi import MIDIPortConnection


class SMPTETimecodeExtension(ExtensionBase):
    """Extension that adds support for reading SMPTE timecode from a
    connection.

    Attributes:
        midi (MIDIConnection): a connection object that yields MIDI messages
    """

    def configure(self, configuration):
        self.midi = create_connection(configuration.get("connection"))
        if not isinstance(self.midi, MIDIPortConnection):
            raise TypeError("{0} supports MIDIPortConnection connections "
                            "only".format(self.__class__.__name__))
        self.midi = reconnecting(self.midi)


construct = SMPTETimecodeExtension
