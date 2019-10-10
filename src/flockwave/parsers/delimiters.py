"""Parser class that assumes that the individual messages in the incoming
stream are separated by a delimiter character that appears in none of the
messages.
"""

from __future__ import absolute_import

from typing import List, Tuple

from .base import ParserBase

__all__ = ("DelimiterBasedParser", "LineParser")


class DelimiterBasedParser(ParserBase[bytes]):
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

    def feed(self, data: bytes) -> List[bytes]:
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

    def _split(self, data: bytes) -> Tuple[bytes, bytes, bytes]:
        """Splits an incoming chunk of data into a prefix, a separator and a
        suffix such that the concatenation of the three parts is always the
        entire data.

        When the incoming chunk does not contain the separator, the prefix will
        contain the whole chunk and the separator and the suffix will be
        empty byte strings.

        Parameters:
            data: the incoming chunk of data

        Returns:
            the prefix, the separator and the suffix
        """
        return data.partition(self.delimiter)


class LineParser(DelimiterBasedParser):
    """Parser class that assumes that the individual messages are delimited
    with newline characters (``\r`` and ``\n``).
    """

    def __init__(self, **kwds):
        super(LineParser, self).__init__(delimiter="\n", **kwds)
        self._trans = bytes.maketrans(b"\r", b"\n")

    def _split(self, data: bytes) -> bytes:
        return bytes.translate(data, self._trans).partition(b"\n")
