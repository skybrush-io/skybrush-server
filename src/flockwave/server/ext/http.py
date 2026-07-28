"""Extension that provides a simple HTTP-based request-response endpoint that
can be used to send one-shot protocol messages to the server and get a quick
response, without establishing a permanent connection.

Only the response to the submitted request will be delivered back to the client.
HTTP authentication headers will be translated to AUTH-REQ requests.
"""

from __future__ import annotations

from contextlib import ExitStack, aclosing
from json import loads
from typing import TYPE_CHECKING, Any, Mapping, TypedDict, cast

from flockwave.encoders import Encoder
from flockwave.encoders.json import create_json_encoder
from pydantic import BaseModel, Field
from quart import Response, abort, request
from trio import (
    BrokenResourceError,
    TooSlowError,
    fail_after,
    open_memory_channel,
    sleep_forever,
)
from trio.abc import ReceiveChannel, SendChannel

from flockwave.server.ext.auth import AuthenticationExtensionAPI
from flockwave.server.ext.http_server import HTTPServerExtensionAPI
from flockwave.server.model import (
    Client,
    CommunicationChannel,
    FlockwaveMessage,
    FlockwaveMessageBuilder,
)
from flockwave.server.utils import overridden
from flockwave.server.utils.quart import make_blueprint

if TYPE_CHECKING:
    from logging import Logger

    from flockwave.server.app import SkybrushServer


app: SkybrushServer | None = None
builder: FlockwaveMessageBuilder | None = None
encoder: Encoder | None = None
log: Logger | None = None


class HTTPConfig(BaseModel):
    """Configuration model for the HTTP request-response extension."""

    route: str = Field(
        default="/api/v1",
        title="URL root",
        description=(
            "URL where the extension is mounted within the HTTP namespace of the server"
        ),
    )


MessageDict = TypedDict(
    "MessageDict",
    {
        "$fw.version": str,
        "id": str,
        "body": dict[str, Any],
    },
    total=True,
)


class HTTPChannel(CommunicationChannel[FlockwaveMessage]):
    """Object that represents an HTTP communication channel between a
    server and a single client.

    The communication channel supports a single in-flight request-response pair only.
    When the responses for a request are being read from the response queue, only the
    response to the submitted request and subsequent `ASYNC-RESP` messages will be
    delivered. Closing the queue frees up the channel to accept a new request and start
    waiting for its response.
    """

    _message_id: str | None
    """ID of the message that we are currently expecting a response for."""

    _queue: SendChannel[FlockwaveMessage] | None
    """Queue via which we can stream the response and any additional `ASYNC-...`
    messages following the primary response back to the client. `None` if we do not
    know the ID of the message that we are waiting for yet.
    """

    def __init__(self):
        """Constructor."""
        self._message_id = None
        self._queue = None

    async def close(self, force: bool = False):
        raise NotImplementedError

    def expect_response_for(
        self, message: MessageDict
    ) -> ReceiveChannel[FlockwaveMessage]:
        """Notifies the communication channel that we are about to send the
        given message and it should prepare for capturing its response so it
        can be forwarded back to the client.
        """
        self._message_id = message["id"]
        self._queue, receive_channel = open_memory_channel[FlockwaveMessage](0)

        return receive_channel

    async def send(self, message: FlockwaveMessage):
        """Handles the delivery of a message sent by the message hub through this
        channel.
        """
        to_enqueue: FlockwaveMessage | None = None

        if self._message_id is not None:
            # We are waiting for the primary response to the message with the given ID
            # so check the "refs" field of each incoming message
            refs = getattr(message, "refs", None)
            if refs is not None and refs == self._message_id:
                # Got the response
                self._message_id = None
                to_enqueue = message

        elif self._queue is not None:
            # Receievd the primary response. From this point onwards we only deliver
            # ASYNC-RESP messages
            body = message.body
            if str(body.get("type")).startswith("ASYNC-"):
                to_enqueue = message

        else:
            # We are not waiting for any messages yet so do nothing
            return

        if to_enqueue:
            if self._queue is not None:
                try:
                    await self._queue.send(to_enqueue)
                except BrokenResourceError:
                    # Consumer is not interested in further messages any more (closed
                    # the receiving end) so we can drop our end as well
                    self._queue = None


############################################################################


def ensure_authorization_header_is_present_if_needed() -> None:
    """Helper function that must be called from a Quart request handler.
    Ensures that the current request has authentication information if
    the server requires authentication.

    Aborts the request with HTTP error 401 if no credentials were presented
    and the server requires authentication.
    """
    global app

    assert app is not None

    if not request.headers.get("Authorization"):
        auth = app.import_api("auth", AuthenticationExtensionAPI)
        if auth.is_required():
            response = Response("Unauthorized", 401)
            headers = response.headers

            for method in auth.get_supported_methods():
                if method == "basic":
                    headers.add("WWW-Authenticate", "Basic")
                elif method == "jwt":
                    headers.add("WWW-Authenticate", "Bearer")

            abort(response)


def wrap_message_in_envelope(message: dict[str, Any]) -> MessageDict:
    """Ensures that the given message has an envelope and possibly returns a
    new message object that includes the Flockwave envelope.
    """
    global builder

    assert builder is not None

    # Generate a unique ID for the message if needed
    if "id" in message:
        id = str(message["id"])
    else:
        id = str(builder.id_generator())

    has_envelope = "$fw.version" in message
    if not has_envelope:
        return {"$fw.version": "1.0", "body": message, "id": id}
    else:
        return cast(MessageDict, message)


def extract_receipts_from_response(body: Any) -> Mapping[str, str]:
    """Extracts the receipt IDs and the corresponding keys from the response body
    if there are any async operations to wait for, and removes them from the response
    body itself.
    """
    if not isinstance(body, dict):
        return {}

    receipts = body.pop("receipt", None)
    if not isinstance(receipts, dict):
        return {}

    return {str(v): str(k) for k, v in receipts.items()}


async def authenticate_client_if_needed(client: Client) -> HTTPChannel:
    """Helper function that injects an AUTH-REQ message and inspects the
    corresponding AUTH-RESP message from the server to decide whether the
    credentials presented by the user are sufficient.

    Aborts the request with HTTP error 403 if the credentials presented by
    the user were not accepted by the server.
    """
    global app

    assert app is not None

    channel = cast(HTTPChannel, client.channel)

    auth = app.import_api("auth", AuthenticationExtensionAPI)
    authorization_header = request.headers.get("Authorization")
    if not authorization_header:
        if auth.is_required():
            abort(403)  # Forbidden
        else:
            return channel

    method, _, data = authorization_header.partition(" ")
    method = method.lower()

    if method == "basic":
        auth_request = {"type": "AUTH-REQ", "method": "basic", "data": data}
    elif method == "bearer":
        auth_request = {"type": "AUTH-REQ", "method": "jwt", "data": data}
    else:
        auth_request = None

    if not auth_request or auth_request["method"] not in auth.get_supported_methods():
        abort(403)  # Forbidden

    auth_request = wrap_message_in_envelope(auth_request)

    async with aclosing(channel.expect_response_for(auth_request)) as queue:
        handled = await app.message_hub.handle_incoming_message(auth_request, client)
        if not handled:
            abort(403)  # Forbidden

        async for message in queue:
            body = message.body
            if body.get("type") != "AUTH-RESP" or body.get("result") is not True:
                abort(403)  # Forbidden

            # Not interested in further messages
            break

    return channel


############################################################################


blueprint = make_blueprint("http", __name__)


@blueprint.route("/", methods=["POST"])
async def index():
    """Request handler that submits a message to the server and waits for the
    response.
    """
    global app

    assert app is not None

    # If authentication is required and we don't have an Authorization header,
    # bail out
    ensure_authorization_header_is_present_if_needed()

    # We only accept JSON messages
    if not request.is_json:
        abort(415)  # Unsupported media type

    # Read the message; the client has 5 seconds to send it
    try:
        with fail_after(5):
            message = await request.get_json()
    except TooSlowError:
        abort(408)  # Request timeout

    # If we did not receive a dict, abort the request
    if not isinstance(message, dict):
        abort(400)  # Bad request

    # Wrap the message in an envelope if needed
    message = wrap_message_in_envelope(message)

    # Create a dummy client in the registry, send the message and wait for the
    # response
    response: FlockwaveMessage | None = None
    receipts: dict[str, str] = {}
    client_id = f"http://{request.host}"
    with app.client_registry.use(client_id, "http") as client:
        channel = await authenticate_client_if_needed(client)

        async with aclosing(channel.expect_response_for(message)) as queue:
            handled = await app.message_hub.handle_incoming_message(message, client)
            if not handled:
                abort(400)  # Bad request

            async for message in queue:
                if response is None:
                    # We have received the primary response to the request
                    response = message
                    receipts.update(extract_receipts_from_response(response.body))
                else:
                    # We have received an ASYNC-RESP message, check if it is one of the
                    # receipts we are waiting for
                    body = message.body
                    type = body.get("type")
                    receipt_id = body.get("id")

                    if type == "ASYNC-ST":
                        key = receipts.get(receipt_id)
                    else:
                        key = receipts.pop(receipt_id, None)

                    if not key:
                        # This is not one of the receipts we are waiting for, ignore it
                        continue

                    match type:
                        case "ASYNC-RESP":
                            # Merge the result or error into the primary response body
                            if "result" in body:
                                response.body.setdefault("result", {})[key] = body[
                                    "result"
                                ]
                            elif "error" in body:
                                response.body.setdefault("error", {})[key] = body[
                                    "error"
                                ]
                            else:
                                response.body.setdefault("error", {})[key] = (
                                    "invalid response"
                                )

                        case "ASYNC-TIMEOUT":
                            # Process timeout error
                            response.body.setdefault("error", {})[key] = "Timeout"

                        case "ASYNC-ST":
                            suspended = bool(body.get("suspended"))
                            if suspended:
                                receipts.pop(receipt_id, None)
                                response.body.setdefault("error", {})[key] = (
                                    "Request suspended"
                                )

                if not receipts:
                    # No receipts to wait for, we are done
                    break

    if encoder is None:
        abort(500)  # Internal server error

    if response is None:
        abort(408)  # Request timeout

    response = loads(encoder(response))
    return response.get("body")


############################################################################


async def run(app: SkybrushServer, configuration: HTTPConfig, logger: Logger):
    """Background task that is active while the extension is loaded."""
    http_server = app.import_api("http_server", HTTPServerExtensionAPI)
    with ExitStack() as stack:
        builder = FlockwaveMessageBuilder()
        encoder = create_json_encoder()

        stack.enter_context(
            overridden(globals(), app=app, builder=builder, encoder=encoder, log=logger)
        )
        stack.enter_context(app.channel_type_registry.use("http", factory=HTTPChannel))
        stack.enter_context(http_server.mounted(blueprint, path=configuration.route))
        await sleep_forever()


dependencies = ("auth", "http_server")
description = "HTTP request-response communication channel"
schema = HTTPConfig
