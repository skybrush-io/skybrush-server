"""Default configuration for the Skybrush gateway server.

This script will be evaluated first when the gateway attempts to load its
configuration. Configuration files may import variables from this module
with `from flockwave.gateway.config import SOMETHING`, and may also modify
them if the variables are mutable.
"""

# IP address on which the gateway will be listening for incoming HTTP requests
HOST = "127.0.0.1"

# Port on which the gateway will be listening for incoming HTTP requests
PORT = 8080

# Maximum number of workers to launch at the same time
MAX_WORKERS = 4

# Secret key used for JWT tokens sent to the gateway when someone wants to
# spin up a new worker. Only tokens signed with this key will be accepted.
JWT_SECRET = "bhu8nji9"

# Set this to a truthy value to make the root URL redirect to aonther address
ROOT_REDIRECTS_TO = None

# Configuration object to use for spawned workers
WORKER_CONFIG = {
    "EXTENSIONS": {
        "auth": {},
        "auth_jwt": {"secret": JWT_SECRET},
        "auto_shutdown": {"timeout": 30},
        "connection_limits": {"max_clients": 1, "max_duration": 3600},
        "frontend": {},
        "http_server": {"host": "", "port": "@PORT@"},
        "show": {},
        "socketio": {},
        "smpte_timecode": {"connection": "midi:IAC Driver Bus 1"},
        "system_clock": {},
        "virtual_uavs": {
            "arm_after_boot": True,
            "count": 5,
            "delay": 0.2,
            "enabled": False,
            "id_format": "{0:02}",
            "origin": [19.062159, 47.473360],  # ELTE kert
            "orientation": 0,
            "takeoff_area": {"type": "grid", "spacing": 5},
        },
    }
}