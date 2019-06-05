from flockwave.server.networking import get_all_ipv4_addresses

import socket


fqdn = None
log = None
server_name = None
zc = None

service_infos = {}

supported_channels = ("tcp", "udp")


def load(app, configuration, logger):
    """Loads the extension."""
    from zeroconf import Zeroconf

    global fqdn, log, server_name, zc

    fqdn = socket.getfqdn()
    log = logger
    server_name = configuration.get("name", fqdn).replace(".", "-")
    zc = Zeroconf()

    channels = app.channel_type_registry

    channels.added.connect(_on_channel_added, sender=channels)
    channels.removed.connect(_on_channel_removed, sender=channels)

    for id in channels.ids:
        if id in supported_channels:
            _register_channel(channels.find_by_id(id))


def _on_channel_added(sender, id, descriptor):
    """Handler that is called when an extension was loaded. We check whether
    it is the `tcp` or `udp` extension and if so, register the corresponding
    service in Zeroconf.
    """
    if id in supported_channels:
        _register_channel(descriptor)


def _on_channel_removed(sender, id, descriptor):
    """Handler that is called when an extension was unloaded. We check whether
    it is the `tcp` or `udp` extension and if so, unregister the corresponding
    service in Zeroconf.
    """
    if id in supported_channels:
        _unregister_channel(descriptor)


def _register_channel(descriptor):
    from zeroconf import ServiceInfo

    global server_name, service_infos, zc

    name = descriptor.id

    if name in service_infos:
        return

    host, port = descriptor.get_address("127.0.0.1")
    if host:
        addresses = [host]
    else:
        addresses = get_all_ipv4_addresses()
    addresses = [socket.inet_aton(address) for address in addresses]

    service = "flockwave"
    service_infos[name] = service_info = ServiceInfo(
        type_=f"_{service}._{name}.local.",
        name=f"{server_name}._{service}._{name}.local.",
        port=port,
        addresses=addresses,
        server=f"{fqdn}.",
    )

    zc.register_service(service_info)


def _unregister_channel(descriptor):
    global service_infos, zc

    name = descriptor.id

    service_info = service_infos.pop(name, None)
    if service_info is None:
        return

    zc.unregister_service(service_info)


def unload(app):
    """Unloads the extension."""
    global zc

    channels = app.channel_type_registry

    channels.added.disconnect(_on_channel_added, sender=channels)
    channels.removed.disconnect(_on_channel_removed, sender=channels)

    zc.close()
    zc = None
