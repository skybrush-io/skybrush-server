"""JSON-RPC protocol parser."""

from tinyrpc import InvalidRequestError
from tinyrpc.protocols import RPCBatchRequest, RPCBatchResponse, RPCRequest, RPCResponse
from tinyrpc.protocols.jsonrpc import JSONRPCProtocol
from typing import Union

from .delimiters import LineParser


RPCMessage = Union[RPCRequest, RPCBatchRequest, RPCResponse, RPCBatchResponse]


class JSONRPCParser(LineParser[RPCMessage]):
    """Parser that parses incoming bytes as JSON-RPC requests and responses.

    It is assumed that individual requests are separated by newlines and that
    no request or response contains a newline character.
    """

    def __init__(self):
        self._protocol = JSONRPCProtocol()
        super().__init__(decoder=self._protocol.parse_request)

    def _parse(self, data: bytes) -> RPCMessage:
        try:
            return self._protocol.parse_request(data)
        except InvalidRequestError as ex:
            try:
                return self._protocol.parse_reply(data)
            except Exception:
                raise ex
