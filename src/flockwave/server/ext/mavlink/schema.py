from .enums import MAVSeverity
from .rssi import RSSIMode

__all__ = ("schema",)


RSSI_MODE_SCHEMA = {
    "type": "string",
    "enum": [
        RSSIMode.NONE.value,
        RSSIMode.RADIO_STATUS.value,
        RSSIMode.RTCM_COUNTERS.value,
    ],
    "title": "RSSI mode",
    "default": RSSIMode.RADIO_STATUS.value,
    "options": {
        "enum_titles": [
            "No RSSI values",
            "From RADIO_STATUS messages",
            "From RTCM counters (Skybrush only)",
        ]
    },
}


NETWORK_PROPERTIES = {
    "connections": {
        "title": "Connection URLs",
        "type": "array",
        "format": "table",
        "items": {"type": "string"},
        "default": [],
        "description": (
            "URLs describing the connections where the server needs to "
            "listen for incoming MAVLink packets in this network. 'default' "
            "means that incoming MAVLink packets are expected on UDP port "
            "14550 and outbound MAVLink packets are sent to UDP port 14555."
        ),
    },
    "id_format": {
        "type": "string",
        "title": "ID format",
        "description": (
            "Python format string that determines the format of the IDs of "
            "the drones created in this network. Overrides the global ID format "
            "defined at the top level."
        ),
    },
    "id_offset": {
        "type": "number",
        "title": "ID offset",
        "default": 0,
        "description": (
            "Offset to add to the numeric ID of each drone within the network "
            "to derive its final ID. You can use it to map multiple networks "
            "with the same MAVLink ID range to different Skybrush ID ranges. "
            "Leave it at zero if you only have one MAVLink network."
        ),
    },
    "network_size": {
        "type": "number",
        "title": "Network size",
        "default": 250,
        "description": (
            "The number of drones that can be connected to this network. "
            "System IDs from 1 to this number (inclusive) will be allocated to "
            "drones within the network."
        ),
    },
    "system_id": {
        "title": "System ID",
        "description": (
            "MAVLink system ID of the server in this network; typically "
            "IDs from 251 to 254 are reserved for ground stations."
        ),
        "type": "integer",
        "minimum": 1,
        "maximum": 255,
        "default": 254,
    },
    "routing": {
        "type": "object",
        "title": "Message routing",
        "properties": {
            "rc": {
                "type": "array",
                "format": "table",
                "title": "RC override",
                "description": "Indices of the connections where RC override messages are routed to (zero-based)",
                "default": [0],
                "items": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                },
            },
            "rtk": {
                "type": "array",
                "format": "table",
                "title": "RTK messages",
                "description": "Indices of the connection where RTK correction messages are routed to (zero-based)",
                "default": [0],
                "items": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                },
            },
        },
    },
    "rssi_mode": dict(
        RSSI_MODE_SCHEMA,
        description="Specifies how RSSI values are derived for the drones in this network",
    ),
    "signing": {
        "type": "object",
        "title": "Message signing",
        "properties": {
            "enabled": {
                "type": "boolean",
                "title": "Enable MAVLink message signing",
                "default": False,
                "format": "checkbox",
                "propertyOrder": -1000,
            },
            "key": {
                "type": "string",
                "title": "Signing key",
                "default": "",
                "description": (
                    "The key must be exactly 32 bytes long. It can be "
                    "provided in hexadecimal format or as a base64-encoded "
                    "string, which is identical to the format being used "
                    "in Mission Planner."
                ),
                "propertyOrder": -500,
            },
            "sign_outbound": {
                "type": "boolean",
                "title": "Sign outbound MAVLink messages if signing is enabled",
                "default": True,
                "format": "checkbox",
            },
            "allow_unsigned": {
                "type": "boolean",
                "title": "Accept unsigned incoming messages",
                "default": False,
                "format": "checkbox",
            },
        },
    },
    "statustext_targets": {
        "type": "object",
        "title": "STATUSTEXT message handling",
        "properties": {
            "client": MAVSeverity.json_schema(
                title="Forward to Skybrush clients above this severity",
            ),
            "server": MAVSeverity.json_schema(
                title="Log in the server log above this severity",
            ),
            "log_prearm": {
                "type": "boolean",
                "title": "Log pre-arm messages",
                "description": (
                    "If enabled, the extension will log all pre-arm check "
                    "errors received from the drones in this network. "
                    "These messages are hidden by default as they are "
                    "fairly common and can be inspected by other means."
                ),
                "default": False,
                "format": "checkbox",
                "propertyOrder": 10000,
            },
        },
        "default": {
            "client": "debug",
            "server": "notice",
            "log_prearm": False,
        },
    },
    "use_broadcast_rate_limiting": {
        "type": "boolean",
        "title": "Apply rate limiting on broadcast messages",
        "description": (
            "This is a workaround that should be enabled only if "
            "you have a connection without flow control and you "
            "are experiencing issues with packet loss, especially "
            "for bursty packet streams like RTK corrections."
        ),
        "default": False,
        "format": "checkbox",
    },
}

schema = {
    "properties": {
        "networks": {
            "title": "MAVLink networks",
            "type": "object",
            "propertyOrder": 2000,
            "options": {"disable_properties": False},
            "additionalProperties": {
                "type": "object",
                "properties": NETWORK_PROPERTIES,
            },
        },
        "id_format": {
            "type": "string",
            "title": "ID format",
            "description": (
                "Python format string that determines the format of the IDs of "
                "the drones created by the extension. May be overridden in each "
                "network."
            ),
            "propertyOrder": 0,
        },
        "custom_mode": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "required": False,
            "title": "Enforce MAVLink custom flight mode",
            "description": (
                "MAVLink custom flight mode number to switch drones to when "
                "they are discovered the first time. 127 is the mode number of "
                "the drone show mode for Skybrush-compatible MAVLink-based "
                "drones. Refer to the documentation of your autopilot for more "
                "details."
            ),
            "default": 127,
            "propertyOrder": 10000,
        },
        "rssi_mode": dict(
            RSSI_MODE_SCHEMA,
            description="Specifies how RSSI values are derived for the drones. May be overridden in each network.",
            propertyOrder=11000,
        ),
        "assume_data_streams_configured": {
            "type": "boolean",
            "title": "Assume that MAVLink packet streams are configured",
            "description": (
                "If enabled, the driver will assume that the MAVLink data streams "
                "are already configured and will not attempt to configure them "
                "automatically. This speeds up the initialization sequence "
                "when you have thousands of drones."
            ),
            "default": False,
            "format": "checkbox",
            "propertyOrder": 12000,
        },
        "autopilot_type": {
            "type": "string",
            "enum": ["auto", "ardupilot", "skybrush", "px4"],
            "title": "Flight controller firmware",
            "default": "auto",
            "options": {
                "enum_titles": [
                    "Autodetected",
                    "ArduPilot",
                    "ArduPilot with Skybrush",
                    "PX4",
                ]
            },
            "propertyOrder": 13000,
        },
        "use_bulk_parameter_uploads": {
            "type": "boolean",
            "title": "Use bulk parameter uploads",
            "description": (
                "If enabled, the driver will use bulk parameter uploads "
                "instead of individual parameter uploads. This can speed up "
                "the parameter upload process, especially for large numbers "
                "of parameters. Requires support from the flight controller; "
                "currently supported by ArduPilot only."
            ),
            "default": False,
            "format": "checkbox",
            "propertyOrder": 14000,
        },
        # packet_loss is an advanced setting and is not included here
    }
}
