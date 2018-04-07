"""Parser class that assumes that the individual messages in the incoming
stream are separated by a delimiter character that appears in none of the
messages.
"""

from __future__ import absolute_import

try:
    from string import maketrans, translate
except ImportError:
    # Python 3.x
    maketrans, translate = bytes.maketrans, bytes.translate

from .base import ParserBase

__all__ = ("DelimiterBasedParser", "LineParser")


class DelimiterBasedParser(ParserBase):
    """Parser class that assumes that the individual messages in the incoming
    stream are separated by a delimiter character that appears in none of the
    messages.
    """

    def __init__(self, **kwds):
        delimiter = kwds.pop("delimiter", "\n")
        min_length = kwds.pop("min_length", 0)

        super(DelimiterBasedParser, self).__init__(**kwds)

        self.delimiter = delimiter
        self.min_length = min_length

        self._chunks = []

    def feed(self, data):
        result = []
        while data:
            prefix, sep, data = self._split(data)
            if prefix:
                self._chunks.append(prefix)
            if sep:
                chunks = b"".join(self._chunks)
                if len(chunks) > self.min_length:
                    success, obj = self._send(chunks)
                    if success:
                        result.append(obj)
                del self._chunks[:]
        return result

    def _split(self, data):
        """Splits an incoming chunk of data into a prefix, a separator and a
        suffix such that the concatenation of the three parts is always the
        entire data.

        When the incoming chunk does not contain the separator, the prefix will
        contain the whole chunk and the separator and the suffix will be
        empty byte strings.

        Parameters:
            data (bytes): the incoming chunk of data

        Returns:
            (bytes, bytes, bytes): the prefix, the separator and the suffix
        """
        return data.partition(self.delimiter)


class LineParser(DelimiterBasedParser):
    """Parser class that assumes that the individual messages are delimited
    with newline characters (``\r`` and ``\n``).
    """

    def __init__(self, **kwds):
        super(LineParser, self).__init__(delimiter="\n", **kwds)
        self._trans = maketrans(b"\r", b"\n")

    def _split(self, data):
        return translate(data, self._trans).partition(b"\n")
