"""Extension that allows the Skybrush server to be discoverable on the
local network with UPnP/SSDP.

The Skybrush server will be represented as a single root device on the
network. The device has no UUID because there might be multiple Skybrush
server instances running on the same machine and it is unclear how the UUID
should be generated in such cases. However, the server will respond to UPnP
M-SEARCH requests for root devices, and for searches for
``urn:collmot-com:device:flockwave`` and
``urn:collmot-com:service:flockwave`` targets.
"""

from contextlib import closing
from datetime import datetime
from errno import EADDRNOTAVAIL, ENODEV
from http.server import BaseHTTPRequestHandler
from logging import Logger
from io import BytesIO
from os import getenv
from random import random
from time import mktime, monotonic
from trio import sleep
from typing import Any, Callable, Dict, Optional
from wsgiref.handlers import format_date_time

import platform
import re
import socket
import struct

from flockwave.networking import create_socket
from flockwave.server.ports import get_port_number_for_service
from flockwave.server.registries import find_in_registry
from flockwave.server.utils import overridden
from flockwave.server.version import __version__ as skybrush_version

from .registry import UPnPServiceRegistry

USN = "flockwave"
UPNP_DEVICE_ID = "urn:collmot-com:device:{0}:1".format(USN)
UPNP_SERVICE_ID_TEMPLATE = "urn:collmot-com:service:{0}-{{0}}:1".format(USN)

app = None
label = None
log: Optional[Logger] = None
registry = None

exports: Dict[str, Optional[Callable[..., Any]]] = {
    "register_service": None,
    "registry": None,
    "unregister_service": None,
    "use_service": None,
}

############################################################################

_RESPONSE_HEADERS = {
    "DATE": lambda: format_date_time(mktime(datetime.now().timetuple())),
    "EXT": "",
    "SERVER": "{0} UPnP/1.1 Skybrush/{1}".format(
        {
            "Linux": "{0}/{2}".format(*platform.uname()),
            "Darwin": "{0}/{2}".format(*platform.uname()),
            "Windows": "{0}/{3}".format(*platform.uname()),
        }.get(platform.system(), platform.system() or "Unknown"),
        skybrush_version,
    ),
    "LABEL.COLLMOT.COM": lambda: label,
}

_UPNP_SERVICE_ID_REGEX = re.compile(
    "^" + UPNP_SERVICE_ID_TEMPLATE.replace("{0}", "([^:]+)") + "$"
)

############################################################################


class Request(BaseHTTPRequestHandler):
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


class Sockets:
    """Simple value object to manage a pair of sockets, one for receiving and
    one for sending.
    """

    def __init__(self):
        self.sender = create_socket(socket.SOCK_DGRAM)
        self.receiver = create_socket(socket.SOCK_DGRAM)

    def close(self):
        """Closees the sockets managed by this object."""
        if self.sender:
            self.sender.close()
            self.sender = None

        if self.receiver:
            self.receiver.close()
            self.receiver = None


def get_service_uri(service_id: str, address=None) -> Optional[str]:
    """Returns the location URI of the UPnP service that belongs to the given
    registered Skybrush service ID.

    Parameters:
        service_id: the ID of the registered Skybrush service; typically a
            channel identifier from the channel type registry
        address: the address of the device requesting the location URI. This
            will be used if the server is listening on multiple interfaces;
            the server tries to ensure that the address returned from this
            function is in the same subnet as the address of the requestor

    Returns:
        the URI of the channel, if known, ``None`` otherwise
    """
    global app

    if app is None:
        return None

    if registry is not None:
        uri = find_in_registry(registry, service_id)
        if uri is not None:
            if callable(uri):
                uri = uri(address)
            return uri

    # service_id is not found in the list of registered services in this
    # extension, so it is most likely a Skybrush channel ID (tcp, udp or
    # websocket)
    try:
        service = app.channel_type_registry.find_by_id(service_id)
    except KeyError:
        service = None

    try:
        location = service.get_ssdp_location(address) if service else None
    except Exception:
        if log:
            log.exception(
                "Failed to retrieve UPnP location of service", extra={"id": service_id}
            )
        location = None

    return location


async def handle_message(message, sender, *, socket):
    """Handles a single message received from the given sender.

    Parameters:
        message (bytes): the incoming message, waiting to be parsed
        sender (Tuple[str,int]): the IP address and port of the sender
    """
    request = Request(message, sender)
    if request.has_error:
        if log:
            log.warn("Malformed SSDP request received")
        return
    elif request.command == "M-SEARCH" and request.path == "*":
        await handle_m_search(request, socket=socket)
    elif request.command == "NOTIFY":
        # We don't care.
        pass
    else:
        if log:
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
    if search_target:
        match = _UPNP_SERVICE_ID_REGEX.match(search_target)
        if match:
            # Need to send a response with the service ID
            channel_type_id = match.group(1)
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


def is_valid_service(service: str) -> bool:
    """Returns whether the service with the given name is a valid service that
    we should respond to in M-SEARCH requests.
    """
    global app, registry

    if app is None:
        return False

    match = _UPNP_SERVICE_ID_REGEX.match(service)
    if not match:
        return False

    service = match.group(1)

    if registry is not None and registry.contains(service):
        return True

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


def load(app, configuration, logger):
    global registry

    # Create a registry to store the registered service IDs
    registry = UPnPServiceRegistry()

    # Set up the functions to export
    exports.update(  # type: ignore
        {
            "register_service": registry.add,
            "registry": registry,
            "unregister_service": registry.remove,
            "use_service": registry.use,
        }
    )


def unload():
    global registry

    exports.update(
        {
            "register_service": None,
            "registry": None,
            "unregister_service": None,
            "use_service": None,
        }
    )

    registry = None


async def run(app, configuration, logger):
    """Loop that listens for incoming messages and calls a handler
    function for each incoming message.
    """
    multicast_group = configuration.get("multicast_group", "239.255.255.250")
    port = configuration.get("port", get_port_number_for_service("ssdp"))
    label = getenv(
        "SKYBRUSH_SSDP_LABEL",
        configuration.get("label", app.config.get("SERVER_NAME")),
    )

    # Set up the extension context
    context = dict(app=app, label=label, log=logger, registry=registry)

    # Set up the socket pair that we will use to send and receive SSDP messages
    sender = create_socket(socket.SOCK_DGRAM)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    await sender.bind(("", 0))

    # Timestamp when the last error message was printed
    last_error = monotonic()

    with overridden(globals(), **context), closing(sender):
        while True:
            try:
                await receive_ssdp_messages(multicast_group, port, sender=sender)
            except OSError as error:
                now = monotonic()

                show_warning = True
                if error.errno == ENODEV and (
                    last_error is None or now - last_error < 5
                ):
                    # "No such device" typically means that the device is not
                    # connected to the network. This is okay, we won't log it
                    # if an error message was logged recently.
                    show_warning = False

                if show_warning:
                    logger.warn("SSDP receiver socket closed: '{}'".format(error))

                last_error = now

            # If we get here, the receiver socket closed for some reason, so
            # we wait a bit and then retry
            await sleep(2)


async def receive_ssdp_messages(multicast_group, port, *, sender):
    global log

    # Set up the receiver end of the socket pair. This is the one that will most
    # likely fail due to various reasons so we will keep on re-trying this if
    # needed
    receiver = create_socket(socket.SOCK_DGRAM)

    # socket.SO_REUSEADDR is not re-exported in trio.socket so import it from
    # socket directly
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    receiver.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    membership_request = struct.pack(
        "4sl", socket.inet_aton(multicast_group), socket.INADDR_ANY
    )

    # TODO(ntamas): make sure that this works even if there is no network
    # connection
    # except OSError in case ad-hoc wifi is not compatible with IP multicast group
    try:
        receiver.setsockopt(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership_request
        )
    except OSError as error:
        if error.errno == EADDRNOTAVAIL:
            # This may happen with ad-hoc wifi on macOS
            if log:
                log.warn(f"Cannot join multicast group {multicast_group}")
        else:
            raise

    await receiver.bind(("", port))
    with closing(receiver):
        while True:
            data = await receiver.recvfrom(65536)
            await handle_message(*data, socket=sender)


def get_schema():
    global app
    return {
        "properties": {
            "label": {
                "type": "string",
                "title": "Service name",
                "description": (
                    "Tick the checkbox to override the default service name used in "
                    "UPnP/SSDP announcements"
                ),
                "default": app.config.get("SERVER_NAME") if app else "",
                "required": False,
                "propertyOrder": 10,
            },
            "multicast_group": {
                "type": "string",
                "title": "Multicast group",
                "description": (
                    "Multicast group to join when listening for UPnP/SSDP discovery "
                    "requests. Tick the checkbox to override the default multicast "
                    "group."
                ),
                "default": "239.255.255.250",
                "required": False,
                "propertyOrder": 20,
            },
            "port": {
                "type": "integer",
                "title": "Port",
                "description": (
                    "Port that the server should listen on for incoming UPnP/SSDP "
                    "discovery requests. Tick the checkbox to override the default "
                    "UPnP/SSDP port."
                ),
                "minimum": 1,
                "maximum": 65535,
                "default": 1900,
                "required": False,
                "propertyOrder": 30,
            },
        },
    }


description = "Automatic server discovery on the local network with UPnP/SSDP"
