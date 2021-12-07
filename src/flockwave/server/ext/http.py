"""Extension that provides a simple HTTP-based request-response endpoint that
can be used to send one-shot protocol messages to the server and get a quick
response, without establishing a permanent connection.

Only the response to the submitted request will be delivered back to the client.
HTTP authentication headers will be translated to AUTH-REQ requests.
"""

from contextlib import ExitStack
from logging import Logger
from json import loads
from quart import abort, Response, request
from trio import Event, fail_after, sleep_forever, TooSlowError
from typing import Any, Dict, List, Optional, Tuple

from flockwave.encoders import Encoder
from flockwave.encoders.json import create_json_encoder
from flockwave.server.model import CommunicationChannel, FlockwaveMessageBuilder
from flockwave.server.utils import overridden
from flockwave.server.utils.quart import make_blueprint

app = None
builder = None
encoder: Optional[Encoder] = None
log: Optional[Logger] = None


class HTTPChannel(CommunicationChannel):
    """Object that represents an HTTP communication channel between a
    server and a single client.

    The communication channel supports a single request-response pair only
    before it is shut down. Only the response to the submitted request will
    be delivered. Authentication-related headers are translated on-the-fly to
    AUTH-REQ messages.
    """

    _event: Optional[Event]
    _message_id: Optional[str]

    def __init__(self):
        """Constructor."""
        self.address = None

        self._event = None
        self._message_id = None
        self._response = None

    def bind_to(self, client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        pass

    async def close(self, force: bool = False):
        raise NotImplementedError

    def expect_response_for(self, message):
        """Notifies the communication channel that we are about to send the
        given message and it should prepare for capturing its response so it
        can be forwarded back to the client.
        """
        if self._message_id != message["id"]:
            self._message_id = message["id"]
            if self._event:
                # in case anyone was waiting for the previous message ID
                self._event.set()
            self._event = Event()

    async def send(self, message):
        """Inherited."""
        refs = getattr(message, "refs", None)
        if refs is not None and refs == self._message_id:
            self._response = message
            if self._event is not None:
                self._event.set()

    async def wait_for_response(self) -> Any:
        assert self._event is not None
        await self._event.wait()
        return self._response


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
        auth = app.import_api("auth")
        if auth.is_required():
            headers: List[Tuple[str, str]] = []
            for method in auth.get_supported_methods():
                if method == "basic":
                    headers.append(("WWW-Authenticate", "Basic"))
                elif method == "jwt":
                    headers.append(("WWW-Authenticate", "Bearer"))

            abort(Response("Unauthorized", 401, headers))


def wrap_message_in_envelope(message: Dict[str, Any]) -> Dict[str, Any]:
    """Ensures that the given message has an envelope and possibly returns a
    new message object that includes the Flockwave envelope.
    """
    global builder

    assert builder is not None

    has_envelope = "$fw.version" in message
    if not has_envelope:
        message = {"$fw.version": "1.0", "body": message}

    # Generate a unique ID for the message if needed
    if "id" not in message:
        message["id"] = str(builder.id_generator())

    return message


async def authenticate_client_if_needed(client) -> None:
    """Helper function that injects an AUTH-REQ message and inspects the
    corresponding AUTH-RESP message from the server to decide whether the
    credentials presented by the user are sufficient.

    Aborts the request with HTTP error 403 if the credentials presented by
    the user were not accepted by the server.
    """
    global app

    assert app is not None

    auth = app.import_api("auth")
    authorization_header = request.headers.get("Authorization")
    if not authorization_header:
        if auth.is_required():
            abort(403)  # Forbidden
        else:
            return

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

    channel = client.channel

    auth_request = wrap_message_in_envelope(auth_request)
    channel.expect_response_for(auth_request)

    handled = await app.message_hub.handle_incoming_message(auth_request, client)
    if not handled:
        abort(403)  # Forbidden

    response = await channel.wait_for_response()
    if response is None:
        abort(408)  # Request timeout

    body = response["body"]
    if body.get("type") != "AUTH-RESP" or body.get("result") is not True:
        abort(403)  # Forbidden


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

    # Wrap the message in an envelope if needed
    message = wrap_message_in_envelope(message)

    # Create a dummy client in the registry, send the message and wait for the
    # response
    client_id = f"http://{request.host}"
    with app.client_registry.use(client_id, "http") as client:
        await authenticate_client_if_needed(client)

        channel = client.channel
        channel.expect_response_for(message)

        handled = await app.message_hub.handle_incoming_message(message, client)
        if not handled:
            abort(400)  # Bad request

        response = await channel.wait_for_response()

    # If we did not get a response, indicate a timeout, otherwise send the
    # response to the client
    if response is None:
        abort(408)  # Request timeout
    elif encoder is None:
        abort(500)  # Internal server error
    else:
        response = loads(encoder(response))
        return response.get("body")


############################################################################


async def run(app, configuration, logger):
    """Background task that is active while the extension is loaded."""
    route = configuration.get("route", "/api/v1")

    http_server = app.import_api("http_server")
    with ExitStack() as stack:
        builder = FlockwaveMessageBuilder()
        encoder = create_json_encoder()

        stack.enter_context(
            overridden(globals(), app=app, builder=builder, encoder=encoder, log=logger)
        )
        stack.enter_context(app.channel_type_registry.use("http", factory=HTTPChannel))
        stack.enter_context(http_server.mounted(blueprint, path=route))
        await sleep_forever()


dependencies = ("auth", "http_server")
description = "HTTP request-response communication channel"
schema = {
    "properties": {
        "route": {
            "type": "string",
            "title": "URL root",
            "description": (
                "URL where the extension is mounted within the HTTP namespace "
                "of the server"
            ),
            "default": "/api/v1",
        }
    }
}
