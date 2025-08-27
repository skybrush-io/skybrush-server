__all__ = ("schema",)

schema = {
    "properties": {
        "add_noise": {
            "title": "Add noise",
            "type": "boolean",
            "description": "Whether to add stochasticity to positions and headings of virtual UAVs",
            "default": False,
        },
        "arm_after_boot": {
            "title": "Arm after boot",
            "type": "boolean",
            "description": "Whether to arm virtual UAVs after boot automatically",
            "default": True,
        },
        "count": {
            "title": "Count",
            "type": "integer",
            "description": "The number of virtual UAVs to generate",
            "minValue": 0,
            "default": 5,
        },
        "delay": {
            "title": "Delay",
            "type": "number",
            "description": (
                "Number of seconds that must pass between two consecutive "
                "simulated status updates to the UAVs"
            ),
            "minValue": 0,
            "default": 0.2,
        },
        "id_format": {
            "title": "ID format",
            "type": "string",
            "description": (
                "Python format string that determines the format of the IDs of "
                "the drones created by this extension"
            ),
            "default": "{0}",
        },
        "orientation": {
            "title": "Orientation",
            "type": "number",
            "description": "Orientation of the virtual UAVs on the ground, in degrees relative to North",
            "default": 59,
        },
        "origin": {
            "title": "Origin",
            "type": "array",
            "description": (
                "Origin (latitude, longitude, altitude in meters) around which "
                "virtual UAVs are placed on the map"
            ),
            "minItems": 3,
            "maxItems": 3,
            "format": "table",
            "items": {"type": "number"},
            "default": [18.915125, 47.486305, 215],
        },
        "takeoff_area": {
            "title": "Takeoff Area",
            "type": "object",
            "description": "Definition of the takeoff area for virtual UAV placement",
            "properties": {
                "spacing": {
                    "title": "Spacing",
                    "type": "number",
                    "description": "The spacing to use between virtual drones, in meters",
                    "minValue": 0,
                    "default": 5,
                },
                "type": {
                    "title": "Type",
                    "type": "string",
                    "enum": ["circle", "line", "grid"],
                    "description": "Type of drone placement shape",
                    "default": "grid",
                    "options": {
                        "enum_titles": [
                            "Place drones along a circle",
                            "Place drones along a line",
                            "Place drones in a grid",
                        ]
                    },
                },
            },
        },
    }
}
