"""Extension that provides UDP socket-based communication channels for the
server.

This extension enables the server to communicate with clients by expecting
requests on a certain UDP port. Responses will be sent to the same host and
port where the request was sent from.
"""

import socket

from eventlet.greenpool import GreenPool
from eventlet import spawn
from greenlet import GreenletExit

from ..encoders import JSONEncoder
from ..model import CommunicationChannel
from ..networking import create_socket, format_socket_address


app = None
encoder = JSONEncoder()
log = None
receiver_thread = None
sock = None


class UDPChannel(CommunicationChannel):
    """Object that represents a UDP communication channel between a
    server and a single client.

    The word "channel" is not really adequate here because UDP is a
    connectionless protocol. That's why notifications are not currently
    handled in this channel - I am yet to figure out how to do this
    properly.
    """

    def __init__(self):
        """Constructor."""
        self.address = None

    def bind_to(self, client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("udp:"):
            host, _, port = client.id[4:].partition(":")
            self.address = host, int(port)
        else:
            raise ValueError("client has no ID or address yet")

    def send(self, message):
        """Inherited."""
        global sock
        sock.sendto(encoder.dumps(message), self.address)

############################################################################


def get_ssdp_location(address):
    """Returns the SSDP location descriptor of the UDP channel."""
    global sock
    if sock:
        return format_socket_address(
            sock, format="udp://{host}:{port}", remote_address=address
        )
    else:
        return None


def handle_message(message, sender):
    """Handles a single message received from the given sender.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        sender (Tuple[str,int]): the IP address and port of the sender
    """
    try:
        message = encoder.loads(message)
    except ValueError as ex:
        log.warn("Malformed JSON message received from {1!r}: {0!r}".format(
            message[:20], sender
        ))
        log.exception(ex)
        return

    client_id = "udp:{0}:{1}".format(*sender)
    with app.client_registry.temporary_client(client_id, "udp") as client:
        app.message_hub.handle_incoming_message(message, client)


def handle_message_safely(message, sender):
    """Handles a single message received from the given sender, ensuring
    that exceptions do not propagate through.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        sender (Tuple[str,int]): the IP address and port of the sender
    """
    try:
        return handle_message(message, sender)
    except GreenletExit:
        return
    except Exception as ex:
        log.exception(ex)


def receive_loop(sock, handler, pool_size=1000):
    """Loop that listens for incoming messages on the given socket and
    calls a handler function for each incoming message.

    Parameters:
        sock (socket.socket): the UDP socket to listen for incoming
            messages
        handler (callable): the function to call with the payload of each
            incoming message. This function will be spawned in a greenlet.
        pool_size (int): number of concurrent UDP requests that the
            extension is willing to handle
    """
    pool = GreenPool(pool_size)
    while True:
        try:
            pool.spawn_n(handle_message_safely, *sock.recvfrom(65536))
        except GreenletExit:
            break
    pool.waitall()


############################################################################


def load(app, configuration, logger):
    """Loads the extension."""
    address = configuration.get("host", ""), configuration.get("port", 5001)
    sock = create_socket(socket.SOCK_DGRAM)
    sock.bind(address)

    app.channel_type_registry.add(
        "udp", factory=UDPChannel,
        ssdp_location=get_ssdp_location
    )

    receiver_thread = spawn(receive_loop, sock, handler=handle_message,
                            pool_size=configuration.get("pool_size", 1000))

    globals().update(
        app=app, log=logger,
        receiver_thread=receiver_thread,
        sock=sock
    )


def unload(app, configuration):
    """Unloads the extension."""
    global receiver_thread

    if receiver_thread:
        receiver_thread.cancel()
        receiver_thread = None

    sock.close()
    app.channel_type_registry.remove("udp")

    globals().update(
        app=None, log=None, sock=None
    )
