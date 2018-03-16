"""Extension that provides TCP socket-based communication channels for the
server.

This extension enables the server to communicate with clients by expecting
requests on a certain TCP port.
"""

import weakref

from eventlet import listen, serve, spawn
from greenlet import GreenletExit

from ..encoders import JSONEncoder
from ..model import CommunicationChannel
from ..networking import format_socket_address

app = None
encoder = JSONEncoder()
log = None
receiver_thread = None
sock = None


class TCPChannel(CommunicationChannel):
    """Object that represents a TCP communication channel between a
    server and a single client.
    """

    def __init__(self):
        """Constructor."""
        self.address = None
        self.socket = None

    def bind_to(self, client):
        """Binds the communication channel to the given client.

        Parameters:
            client (Client): the client to bind the channel to
        """
        if client.id and client.id.startswith("tcp:"):
            host, _, port = client.id[4:].partition(":")
            self.address = host, int(port)
            self.client_ref = weakref.ref(client, self._erase_socket)
        else:
            raise ValueError("client has no ID or address yet")

    def send(self, message):
        """Inherited."""
        if self.socket is None:
            self.socket = self.client_ref().socket
            self.client_ref = None
        self.socket.send(encoder.dumps(message))
        self.socket.send(b"\n")

    def _erase_socket(self, ref):
        self.socket = None

############################################################################


def get_ssdp_location(address):
    """Returns the SSDP location descriptor of the TCP channel."""
    global sock
    if sock:
        return format_socket_address(
            sock, format="tcp://{host}:{port}", remote_address=address
        )
    else:
        return None


def handle_connection(sock, address):
    """Handles a connection attempt from a given client.

    Parameters:
        sock (socket.socket): the socket that can be used to communicate
            with the client
        address (Tuple[str,int]): the IP address and port of the client
    """
    client_id = "tcp:{0}:{1}".format(*address)
    with app.client_registry.temporary_client(client_id, "tcp") as client:
        client.socket = sock
        chunks = []
        while True:
            data = sock.recv(1024)
            if data:
                pos = data.find(b"\n")
                if pos >= 0:
                    pos += 1
                    chunks.append(data[:pos])
                    message = b"".join(chunks)
                    handle_message_safely(message, client)
                    chunks[:] = [data[pos:]] if pos < len(data) else []
                else:
                    chunks.append(data)
            else:
                return


def handle_message(message, client):
    """Handles a single message received from the given sender.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        client (Client): the client that sent the message
    """
    try:
        message = encoder.loads(message.decode("utf-8"))
    except ValueError as ex:
        log.warn("Malformed JSON message received from {1!r}: {0!r}".format(
            message[:20], client.id
        ))
        log.exception(ex)
        return
    app.message_hub.handle_incoming_message(message, client)


def handle_message_safely(message, client):
    """Handles a single message received from the given sender, ensuring
    that exceptions do not propagate through.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        client (Client): the client that sent the message
    """
    try:
        return handle_message(message, client)
    except GreenletExit:
        return
    except Exception as ex:
        log.exception(ex)


############################################################################


def load(app, configuration, logger):
    """Loads the extension."""
    address = configuration.get("host", ""), configuration.get("port", 5001)
    sock = listen(address)

    app.channel_type_registry.add(
        "tcp", factory=TCPChannel,
        ssdp_location=get_ssdp_location
    )

    receiver_thread = spawn(serve, sock, handle=handle_connection,
                            concurrency=configuration.get("pool_size", 1000))

    globals().update(
        app=app, log=logger, sock=sock,
        receiver_thread=receiver_thread
    )


def unload(app, configuration):
    """Unloads the extension."""
    global receiver_thread

    if receiver_thread:
        receiver_thread.cancel()
        receiver_thread = None

    app.channel_type_registry.remove("tcp")

    globals().update(
        app=None, log=None
    )
