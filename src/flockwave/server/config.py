"""Default configuration for the Skybrush server.

This script will be evaluated first when the server attempts to load its
configuration. Configuration files may import variables from this module
with `from flockwave.server.config import SOMETHING`, and may also modify
them if the variables are mutable. For instance, to disable an extension
locally, create a configuration file containing this:

    from flockwave.server.config import EXTENSIONS
    del EXTENSIONS["extension_to_disable"]
"""

# Label that is used to identify the server in SSDP discovery requests
SERVER_NAME = "Skybrush server"

# Base port from which the port numbers used by the server are derived
PORT = 5000

# Configure the command execution manager
COMMAND_EXECUTION_MANAGER = {"timeout": 90}

# Declare the list of extensions to load
EXTENSIONS = {
    "audit_log": {"enabled": "avoid"},
    "auth": {},
    "auth_basic": {"enabled": False},
    "beacon": {},
    "clocks": {},
    "console_status": {},
    "crazyflie": {
        "id_format": "{0:02}",
        "connections": ["crazyradio://0/80/2M/E7E7E7E7"],
        "debug": False,
        "enabled": False,
        "fence": {"enabled": True, "distance": 1, "action": "none"},
        "status_interval": 0.5,  # number of seconds between consecutive status reports from a drone
        "takeoff_altitude": 1.0,
        "testing": False,
    },
    "debug": {
        "enabled": False,
    },
    "ext_manager": {},
    "firmware_update": {},  # used to trigger auto-loading when the license is installed
    "frontend": {},
    "gps": {
        "connection": "gpsd",
        "enabled": False,
        "id_format": "GPS:{0}",
    },
    "hotplug": {},
    "http": {},
    "http_server": {},
    "insomnia": {"keep_display_on": False},
    "kp_index": {"source": "potsdam"},
    "license": {},
    "location": {},
    "location_from_uavs": {"priority": 0},
    "lps": {"enabled": "avoid"},
    "logging": {"keep": 7, "format": "tabular", "size": 1000000},
    "magnetic_field": {},
    "map_cache": {},  # used to trigger auto-loading when the license is installed
    "mavlink": {
        "enabled": False,
        "id_format": "{0:02}",
        "networks": {
            "mav": {
                "connections": [
                    "default"
                ],  # default setup; listens for heartbeats on UDP port 14550, sends broadcasts to UDP port 14555
                "routing": {},
            }
        },
    },
    "missions": {},
    "motion_capture": {"enabled": "avoid", "frame_rate": 10},
    "rc": {"enabled": "avoid"},
    "rc_udp": {"enabled": False},
    "rtk": {
        "presets": {},
        "add_serial_ports": True,
        "message_set": "basic",
        # "gnss_types": "all",  # or a list like ["gps", "glonass"]
        "use_high_precision": True,  # set to false if the rover cannot handle high-precision MSM RTK messages
    },
    "show": {
        "default_start_method": "rc",  # set to "auto" if you typically start shows automatically and not via a remote controller
        "point_of_no_return_seconds": -10,
    },
    "show_pro": {},  # used to trigger auto-loading when the license is installed
    "sidekick": {},  # used to trigger auto-loading when the license is installed
    "smpte_timecode": {},  # used to trigger auto-loading when the license is installed
    "socketio": {},
    "ssdp": {},
    "studio": {},  # used to trigger auto-loading when the license is installed
    "system_clock": {},
    "tcp": {},
    "udp": {},
    "virtual_uavs": {
        "arm_after_boot": True,
        "add_noise": False,
        "count": 5,
        "delay": 0.2,
        "enabled": False,
        "id_format": "{0}",
        "origin": [18.915125, 47.486305, 215],  # Fahegy
        # "origin": [19.062159, 47.473360],  # ELTE kert
        "orientation": 59,
        "takeoff_area": {"type": "grid", "spacing": 5},
    },
    "weather": {},
    "webui": {
        "enabled": True,
        # "route": "/webui",
    },
}
