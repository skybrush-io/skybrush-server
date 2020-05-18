"""Extension that provides a simple HTTP-based request-response endpoint that
can be used to send one-shot protocol messages to the server and get a quick
response, without establishing a permanent connection.

Only the response to the submitted request will be delivered back to the client.
HTTP authentication headers will be translated to AUTH-REQ requests.
"""

from contextlib import ExitStack
from json import loads
from quart import abort, Blueprint, request
from trio import Event, fail_after, sleep_forever, TooSlowError
from typing import Any, Tuple

from flockwave.encoders.json import create_json_encoder
from flockwave.server.message_hub import MessageValidationError
from flockwave.server.model import CommunicationChannel, FlockwaveMessageBuilder
from flockwave.server.utils import overridden


app = None
builder = None
encoder = None
log = None


class HTTPChannel(CommunicationChannel):
    """Object that represents an HTTP communication channel between a
    server and a single client.

    The communication channel supports a single request-response pair only
    before it is shut down. Only the response to the submitted request will
    be delivered. Authentication-related headers are translated on-the-fly to
    AUTH-REQ messages.
    """

    def __init__(self):
        """Constructor."""
        self.address = None

        self._event = Event()
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

    async def send(self, message):
        """Inherited."""
        refs = message.refs
        if refs is not None and refs == self._message_id:
            self._response = message
            self._event.set()

    async def wait_for_response(self) -> Any:
        await self._event.wait()
        return self._response


############################################################################


blueprint = Blueprint("http", __name__)


@blueprint.route("/", methods=["POST"])
async def index():
    """Request handler that submits a message to the server and waits for the
    response.
    """
    global app

    if not request.is_json:
        abort(415)  # Unsupported media type

    try:
        with fail_after(5):
            message = await request.get_json()
    except TooSlowError:
        abort(408)  # Request timeout

    has_envelope = "$fw.version" in message
    if not has_envelope:
        message = {"$fw.version": "1.0", "body": message}

    if "id" not in message:
        message["id"] = str(builder.id_generator())

    client_id = f"http://{request.host}"
    with app.client_registry.use(client_id, "http") as client:
        client.channel._message_id = message["id"]

        handled = await app.message_hub.handle_incoming_message(message, client)
        if not handled:
            abort(400)  # Bad request

        response = await client.channel.wait_for_response()

    response = loads(encoder(response))
    return response if has_envelope else response.get("body")


async def handle_message(message: Any, sender: Tuple[str, int]) -> None:
    """Handles a single message received from the given sender.

    Parameters:
        message: the incoming message
        sender: the IP address and port of the sender
    """
    client_id = "udp://{0}:{1}".format(*sender)

    with app.client_registry.use(client_id, "udp") as client:
        await app.message_hub.handle_incoming_message(message, client)


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


dependencies = ("http_server",)
