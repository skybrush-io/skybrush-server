"""Classes for JSON-RPC message encoding."""

from __future__ import absolute_import

from tinyrpc.protocols.jsonrpc import (
    JSONRPCRequest,
    JSONRPCSuccessResponse,
    JSONRPCErrorResponse,
)
from typing import Union

from .base import Encoder
from .json import JSONEncoder

__all__ = ("JSONRPCEncoder",)


JSONRPCMessage = Union[JSONRPCRequest, JSONRPCSuccessResponse, JSONRPCErrorResponse]


class JSONRPCEncoder(Encoder[JSONRPCMessage]):
    """Custom JSON- encoder function to be used by JSON-RPC communication
    channels.

    The JSON format used by this encoder ensures that there are no newlines
    in the encoded JSON objects. Therefore, newlines can safely be used as
    message delimiters.
    """

    def __init__(self, encoding="utf-8"):
        """Constructor.

        Parameters:
            encoding: encoding to use in the output
        """
        self._json_encoder = JSONEncoder(encoding)

    def dumps(self, obj: JSONRPCMessage) -> bytes:
        return self._json_encoder.dumps(obj._to_dict())

    def loads(self, data: bytes):
        raise NotImplementedError

    encode = dumps
