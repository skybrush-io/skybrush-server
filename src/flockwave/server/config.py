"""Default configuration for the Skybrush server.

This script will be evaluated first when the server attempts to load its
configuration. Configuration files may import variables from this module
with `from flockwave.server.config import SOMETHING`, and may also modify
them if the variables are mutable. For instance, to disable an extension
locally, create a configuration file containing this:

    from flockwave.server.config import EXTENSIONS
    del EXTENSIONS["extension_to_disable"]
"""

import platform

ON_MAC = platform.system().lower() == "darwin"

# Label that is used to identify the server in SSDP discovery requests
SERVER_NAME = "Skybrush server"

# Secret key to encode cookies and session data
SECRET_KEY = (
    b"\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef"
)

# Configure the command execution manager
COMMAND_EXECUTION_MANAGER = {"timeout": 30}

# Declare the list of extensions to load
EXTENSIONS = {
    "auth": {},
    "auth_basic": {"enabled": False, "passwords": {"user@domain.xyz": "password"}},
    "cascade_demo": {
        "enabled": False,
        # Farkashegy
        "stations": {
            "A": [18.9157319, 47.4848304],
            "B": [18.9150453, 47.4857730],
            "C": [18.9164507, 47.4856715],
            # "D": [18.915955, 47.486647],
            # "E": [18.917274, 47.485178],
        },
        "waypoints": {
            "a": [18.9157748, 47.4854612],
            "b": [18.9163113, 47.4850407],
            "c": [18.9165688, 47.4853815],
        },
        "routes": {"A->B": [], "A->C": ["b", "c"], "B->C": ["a"]},
    },
    "crazyflie": {
        "id_format": "{0:02}",
        "connections": ["crazyradio://0/80/2M/E7E7E7E7"],
        "enabled": False,
    },
    "debug": {},
    "dock": {"enabled": False, "listener": "unix:/tmp/flockwaved-dock.sock"},
    "flockctrl": {
        "id_format": "{0:02}",
        "connections": {
            "wireless": "default",
            # "wireless": "local",
            # "wireless": "192.168.1.0/24",
            "radio": "default",
        },
    },
    "gps": {
        # "connection": "/dev/cu.usbmodem1411",
        "connection": "gpsd",
        "enabled": False,
        "id_format": "BEACON:{0}",
    },
    "hotplug": {},
    "http": {},
    "http_server": {},
    "mavlink": {
        "enabled": False,
        "id_format": "{0:02}",
        # "connections": ["tcp://localhost:5760"],
        # "connections": ["udp://localhost:14550"],
        "connections": ["udp-broadcast://:14550"],
        "custom_mode": None,
    },
    "radiation": {
        "enabled": False,
        "sources": [{"lat": 47.473703, "lon": 19.061739, "intensity": 50000}],
        "background_intensity": 10,
    },
    "rtk": {
        "presets": {
            "elte-rtk": {
                "title": "ELTE RTK",
                "source": "ntrip://elte5:5etle@geotradegnss.hu/BUDA.RTCM3",
                "filter": {"reject": ["rtcm3/1007", "rtcm3/1019"]},
            },
            "javad": {
                "title": "JAVAD Triumph-2",
                "source": "tcp://192.168.47.1:8010",
                "format": "rtcm3",  # can be rtcm2, rtcm3 or auto
            },
        },
        "add_serial_ports": True,
    },
    "show": {},
    "socketio": {},
    # "smpte_timecode": {"connection": "midi:IAC Driver Bus 1"},
    "ssdp": {},
    "system_clock": {},
    "tcp": {},
    "udp": {},
    "virtual_uavs": {
        "arm_after_boot": True,
        "count": 5,
        "delay": 0.2,
        "enabled": False,
        "id_format": "{0:02}",
        "origin": [18.915125, 47.486305, 215],  # Fahegy
        # "origin": [19.062159, 47.473360],  # ELTE kert
        "orientation": 59,
        "takeoff_area": {"type": "grid", "spacing": 5},
    },
}

# smpte_timecode seems to have some problems on a Mac - it consumes 15% CPU
# even when idle, and it starts throwing messages like this on the console
# after a while if there is no MIDI device:
#
# MidiInCore::initialize: error creating OS-X MIDI client object (-50)
if ON_MAC:
    EXTENSIONS.pop("smpte_timecode", None)
