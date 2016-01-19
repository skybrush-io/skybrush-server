"""Extension that creates one or more fake UAVs in the server.

Useful primarily for debugging purposes and for testing the server without
having access to real hardware that provides UAV position and velocity data.
"""

from flockwave.server.model import UAVStatusInfo

__all__ = ()

app = None
log = None
uavs = []


def load(current_app, configuration, logger):
    global app
    app = current_app

    count = configuration.get("count", 0)
    id_format = configuration.get("id_format", "FAKE-{0}")
    uavs = [id_format.format(index) for index in xrange(count)]
    for uav_id in uavs:
        app.uav_registry.update_uav_status(uav_id, None)
