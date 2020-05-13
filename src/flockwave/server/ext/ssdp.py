"""Extension that allows the Flockwave server to be discoverable on the
local network with UPnP/SSDP.

The Flockwave server will be represented as a single root device on the
network. The device has no UUID because there might be multiple Flockwave
server instances running on the same machine and it is unclear how the UUID
should be generated in such cases. However, the server will respond to UPnP
M-SEARCH requests for root devices, and for searches for
``urn:collmot-com:device:flockwave`` and
``urn:collmot-com:service:flockwave`` targets.
"""

from contextlib import closing
from datetime import datetime
from io import BytesIO
from os import getenv
from random import random
from six.moves import BaseHTTPServer
from time import mktime
from trio import sleep
from wsgiref.handlers import format_date_time

import platform
import re
import struct
import trio.socket

from flockwave.networking import create_socket
from flockwave.server.utils import overridden
from flockwave.server.version import __version__ as flockwave_version

USN = "flockwave"
UPNP_DEVICE_ID = "urn:collmot-com:device:{0}:1".format(USN)
UPNP_SERVICE_ID_TEMPLATE = "urn:collmot-com:service:{0}-{{0}}:1".format(USN)

app = None
label = None
log = None

############################################################################

_RESPONSE_HEADERS = {
    "DATE": lambda: format_date_time(mktime(datetime.now().timetuple())),
    "EXT": "",
    "SERVER": "{0} UPnP/1.1 Flockwave/{1}".format(
        {
            "Linux": "{0}/{2}".format(*platform.uname()),
            "Darwin": "{0}/{2}".format(*platform.uname()),
            "Windows": "{0}/{3}".format(*platform.uname()),
        }.get(platform.system(), platform.system() or "Unknown"),
        flockwave_version,
    ),
    "LABEL.COLLMOT.COM": lambda: label,
}

_UPNP_SERVICE_ID_REGEX = re.compile(
    "^" + UPNP_SERVICE_ID_TEMPLATE.replace("{0}", "([^:]+)") + "$"
)

############################################################################


class Request(BaseHTTPServer.BaseHTTPRequestHandler):
    """Class for parsing the contents of an incoming SSDP request (which is
    essentially a glorified HTTP request so the same parser can be used).
    """

    def __init__(self, request, client_address):
        """Constructor.

        Parameters:
            data (bytes): the body of the request
            sender (Tuple[str, int]): the sender of the request
        """
        self.client_address = client_address
        self.rfile = BytesIO(request)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    @property
    def has_error(self):
        """Returns whether the request has an associated error code."""
        return self.error_code is not None

    def send_error(self, code, message):
        """Records an HTTP error code and message in the current request
        object. These errors typically come from the parser.
        """
        self.error_code = code
        self.error_message = message


class Sockets(object):
    """Simple value object to manage a pair of sockets, one for receiving and
    one for sending.
    """

    def __init__(self):
        self.sender = create_socket(trio.socket.SOCK_DGRAM)
        self.receiver = create_socket(trio.socket.SOCK_DGRAM)

    def close(self):
        """Closees the sockets managed by this object."""
        if self.sender:
            self.sender.close()
            self.sender = None

        if self.receiver:
            self.receiver.close()
            self.receiver = None


def get_service_uri(channel_id, address=None):
    """Returns the location URI of the UPnP service that belongs to the given
    registered Flockwave communication channel.

    Parameters:
        channel_id (str): the ID of the Flockwave channel from the channel type
            registry

    Returns:
        Optional[str]: the URI of the channel, if known, ``None`` otherwise
    """
    global app

    if app is None:
        return None

    try:
        service = app.channel_type_registry.find_by_id(channel_id)
    except KeyError:
        service = None

    return service.get_ssdp_location(address) if service else None


async def handle_message(message, sender, *, socket):
    """Handles a single message received from the given sender.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        sender (Tuple[str,int]): the IP address and port of the sender
    """
    request = Request(message, sender)
    if request.has_error:
        log.warn("Malformed SSDP request received")
        return
    elif request.command == "M-SEARCH" and request.path == "*":
        await handle_m_search(request, socket=socket)
    elif request.command == "NOTIFY":
        # We don't care.
        pass
    else:
        log.warn("Unknown SSDP command: {0.command}".format(request))


async def handle_m_search(request, *, socket):
    """Handles an incoming M-SEARCH request.

    Parameters:
        request (Request): the incoming request to handle
    """
    global _RESPONSE_HEADERS, _UPNP_SERVICE_ID_REGEX, UPNP_DEVICE_ID

    if request.headers.get("MAN") != '"ssdp:discover"':
        return

    # Get the wait time from the request
    wait_time = min(5, int(request.headers.get("MX", 0)))
    if wait_time <= 0:
        # Request must be ignored
        return

    # Find out whether we should respond to the request at all.
    # We may have to send multiple responses with various values of the
    # ST field. Prepare the list of ST values that we need to send.
    search_target = request.headers.get("ST")
    address = request.client_address
    to_send = []
    if search_target in ("ssdp:all", "upnp:rootdevice", UPNP_DEVICE_ID):
        # Need to send a response with the device ID
        to_send.append((UPNP_DEVICE_ID, "unknown"))
    if search_target and _UPNP_SERVICE_ID_REGEX.match(search_target):
        # Need to send a response with the service ID
        channel_type_id = _UPNP_SERVICE_ID_REGEX.match(search_target).group(1)
        to_send.append((search_target, get_service_uri(channel_type_id, address)))

    # TODO(ntamas): for ssdp:all, we need to enumerate all services explicitly

    # Sleep a bit according to specs
    await sleep(random() * wait_time)

    # Prepare response
    for search_target, location in to_send:
        if location is None:
            continue

        response = prepare_response(
            ["CACHE-CONTROL", "DATE", "EXT", "LABEL.COLLMOT.COM", "SERVER", "USN"],
            extra={"LOCATION": location, "ST": search_target},
            prefix=request.request_version + " 200 OK",
        )
        try:
            await socket.sendto(response, request.client_address)
        except OSError:
            # Okay, maybe the network went down in the meanwhile, let's just
            # ignore this error and move on
            pass


def is_valid_service(service):
    """Returns whether the service with the given name is a valid service that
    we should respond to in M-SEARCH requests.
    """
    global app

    if app is None:
        return False

    match = _UPNP_SERVICE_ID_REGEX.match(service)
    if not match:
        return False

    service = match.group(1)
    print(repr(service))
    channel = app.channel_type_registry.find_by_id(service)
    return channel and channel.get_ssdp_location() is not None


def prepare_response(headers=None, extra=None, prefix=None):
    """Prepares a response to send.

    Parameters:
        headers (Iterable[str]): list containing names of standard headers to
            include in the response. The values of the headers are obtained
            from the global ``_RESPONSE_HEADERS`` dictionary. When the dict
            contains a function for a given header name, the function will be
            executed without arguments and its return value will be added as
            the real value of the header.
        extra (Dict[str,str]): dictionary mapping additional headers to add
            to the response.
        prefix (str): prefix line to add in front of the response.
    """
    response = [prefix]

    if headers:
        for header_name in headers:
            header_name = header_name.upper()
            header_value = _RESPONSE_HEADERS.get(header_name)
            if callable(header_value):
                header_value = header_value()
            if header_value is None:
                continue
            response.append("{0}: {1}".format(header_name, header_value))

    if extra:
        response.extend("{0}: {1}".format(*pair) for pair in extra.items())

    response.append("")
    response.append("")
    return "\r\n".join(response).encode("ascii")


############################################################################


async def run(app, configuration, logger):
    """Loop that listens for incoming messages and calls a handler
    function for each incoming message.
    """
    multicast_group = configuration.get("multicast_group", "239.255.255.250")
    port = configuration.get("port", 1900)
    label = getenv(
        "FLOCKWAVE_SSDP_LABEL",
        configuration.get("label", app.config.get("SERVER_NAME")),
    )

    # Set up the socket pair that we will use to send and receive SSDP messages
    sender = create_socket(trio.socket.SOCK_DGRAM)
    sender.setsockopt(trio.socket.IPPROTO_IP, trio.socket.IP_MULTICAST_TTL, 2)
    await sender.bind(("", 0))

    receiver = create_socket(trio.socket.SOCK_DGRAM)
    receiver.setsockopt(trio.socket.IPPROTO_IP, trio.socket.IP_MULTICAST_TTL, 2)
    membership_request = struct.pack(
        "4sl", trio.socket.inet_aton(multicast_group), trio.socket.INADDR_ANY
    )
    # TODO(ntamas): make sure that this works even if there is no network
    # connection
    # except OSError in case ad-hoc wifi is not compatible with IP multicast group
    try:
        receiver.setsockopt(
            trio.socket.IPPROTO_IP, trio.socket.IP_ADD_MEMBERSHIP, membership_request
        )
    except OSError as error:
        logger.warn("OSError while calling receiver.setsockopt(): '{}'"
            .format(error)
        )
        
    await receiver.bind((multicast_group, port))

    context = dict(app=app, label=label, log=logger)

    with overridden(globals(), **context), closing(sender), closing(receiver):
        while True:
            data = await receiver.recvfrom(65536)
            await handle_message(*data, socket=sender)
