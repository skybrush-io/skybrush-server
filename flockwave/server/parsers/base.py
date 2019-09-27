"""Base class and interface specification for message parsers in the
Flockwave server.
"""

from __future__ import absolute_import

from abc import ABCMeta, abstractmethod

__all__ = ("Parser",)


class Parser(metaclass=ABCMeta):
    """Interface specification for message parsers that can be fed with incoming
    data and that call a specific callback function whenever they are able to
    parse a new message out of the incoming data.

    Attributes:
        callback (callable): the function to call when the parser has detected
            a message on the input stream. The function will be called with
            the detected message as the first argument. When no decoder is
            attached to the parser, the detected message will be the raw
            body of the message. When a decoder is attached to the parser, the
            message will be given to the decoder and the return value will be
            given to the callback.
        decoder (Optional[callable]): optional function to call on each detected
            incoming message before it is given to the callback as the first
            argument. The return value of the function will be given to the
            callback instead of the incoming message.
        filter (Optional[callable]): optional function to call on each detected
            incoming message before it is given to the decoder. The function
            must return ``True`` or ``False``; if it returns ``False``, the
            message will be dropped.
    """

    @abstractmethod
    def feed(self, data):
        """Feeds the parser with the given raw incoming bytes.

        Parameters:
            data (bytes): the raw bytes to feed into the parser

        Returns:
            List[object]: a list of parsed messages from the current chunk
        """
        raise NotImplementedError


class ParserBase(Parser):
    """Base class for parsers."""

    def __init__(self, **kwds):
        """Constructor.

        Parameters:
            callback (Optional[callable]): the function to call when the parser
                has detected a message on the input stream.
            decoder (Optional[callable]): optional function to call on each
                detected incoming message before it is given to the callback as
                the first argument. The return value of the function will be
                given to the callback instead of the incoming message.
            filter (Optional[callable]): optional function to call on each
                detected incoming message before it is given to the decoder.
                The function must return ``True`` or ``False``; if it
                returns ``False``, the message will be dropped.
        """
        self.callback = kwds.get("callback")
        self.decoder = kwds.get("decoder")
        self.filter = kwds.get("filter")

    def _send(self, data):
        """Given a chunk of data that is most likely a valid message, sends
        it through the decoder (if any) and then feeds the result to the
        callback function.

        Returns:
            (bool, object): whether an object was parsed successfully, and if
                so, the object itself
        """
        if self.filter and not self.filter(data):
            return False, None
        if self.decoder:
            data = self.decoder(data)
        if self.callback:
            self.callback(data)
        return True, data
