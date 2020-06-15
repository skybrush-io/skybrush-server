"""String templates to be used for parametrized mission file generation for
the flockctrl system.
"""

from base64 import b64decode
from importlib.resources import read_text
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

from flockwave.gps.vectors import FlatEarthToGPSCoordinateTransformation

__all__ = ("get_template", "gps_coordinate_to_string")


_template_pkg = __name__.rpartition(".")[0] + ".templates"


def get_template(name: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Returns the contents of a template file from the `templates/` subdirectory,
    used for mission generation.

    Parameters:
        name: name of the template file
        encoding: the encoding of the file
        errors: specifies how to handle encoding errors in the input file;
            forwarded directly to `importlib.resources.read_text()`.

    Returns:
        the loaded template file
    """
    return read_text(_template_pkg, name, encoding=encoding, errors=errors)


def generate_mission_file_from_show_specification(show) -> bytes:
    """Generates a full uploadable mission ZIP file from a drone light show
    specification in Skybrush format.

    Returns:
        the uploadable mission ZIP file as a raw bytes object
    """
    # TODO: move this to a proper place, I do not know where...
    # TODO: generalize all conversions in flockwave.gps.vectors
    def to_neu(pos, type_string):
        """Convert a flat earth coordinate to 'neu' type."""
        if type_string == "neu":
            pos_neu = (pos[0], pos[1], pos[2])
        elif type_string == "nwu":
            pos_neu = (pos[0], -pos[1], pos[2])
        elif type_string == "ned":
            pos_neu = (pos[0], pos[1], -pos[2])
        elif type_string == "nwd":
            pos_neu = (pos[0], -pos[1], -pos[2])
        else:
            raise NotImplementedError("GPS coordinate system type unknown.")

        return pos_neu

    # parse coordinate system
    coordinate_system = show.get("coordinateSystem")
    try:
        trans = FlatEarthToGPSCoordinateTransformation.from_json(coordinate_system)
    except Exception:
        raise RuntimeError("Invalid or missing coordinate system specification")

    # parse home coordinate
    if "home" in show:
        home = to_neu(show["home"], trans.type)
    else:
        raise RuntimeError("No home coordinate in show specification")

    # parse trajectory
    if "trajectory" in show:
        trajectory = show["trajectory"]
        takeoff_time = trajectory["takeoffTime"]
        points = trajectory["points"]
    else:
        raise RuntimeError("No trajectory in show specification")

    # create waypoints
    last_t = 0
    waypoints = []
    for t, p, _ in points:
        # add takeoff time to waypoints
        t += takeoff_time
        # convert position to NEU
        pos = to_neu(p, trans.type)
        waypoints.append(
            "waypoint={x} {y} {z} {vxy} {vz} T{t} 6".format(
                x=pos[0],
                y=pos[1],
                z=pos[2],
                vxy=8,  # TODO: get from show
                vz=2.5,  # TODO: get from show
                t=t - last_t,
            )
        )
        last_t = t

    # create waypoint file template
    waypoint_str = get_template("waypoints.cfg").format(
        angle=trans.orientation,
        ground_altitude=0,  # TODO: use this if needed
        origin=gps_coordinate_to_string(lat=trans.origin.lat, lon=trans.origin.lon),
        waypoints="\n".join(waypoints),
    )

    # create empty waypoint file template
    waypoint_ground_str = get_template("waypoints.cfg").format(
        angle=trans.orientation,
        ground_altitude=0,  # TODO: use this if needed
        origin=gps_coordinate_to_string(lat=trans.origin.lat, lon=trans.origin.lon),
        waypoints="waypoint={} {} -100 4 2 1000 6".format(home[0], home[1]),
    )

    # create mission files
    mission_str = get_template("mission.cfg")

    # create choreography file
    choreography_str = get_template("choreography_show.cfg").format(
        altitude_setpoint=5,  # TODO: get from show if needed
        velocity_xy=8,  # TODO: get from show
        velocity_z=2.5,  # TODO: get from show
    )

    # parse lights
    lights = show.get("lights", None)
    light_data = b64decode(lights["data"])

    # create mission.zip
    # create the zipfile and write content to it
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zip_archive:
        zip_archive.writestr("waypoints.cfg", waypoint_str)
        zip_archive.writestr("waypoints_ground.cfg", waypoint_ground_str)
        zip_archive.writestr("choreography.cfg", choreography_str)
        zip_archive.writestr("mission.cfg", mission_str)
        zip_archive.writestr("light_show.bin", light_data)
        zip_archive.writestr("_meta/version", "1")
        zip_archive.writestr("_meta/name", show.get("name", "drone-show"))
        zip_archive.close()

    return buffer.getvalue()


def gps_coordinate_to_string(lat: float, lon: float) -> str:
    """Return a string to be used in waypoint files when absolute coordinates
    are needed.

    Parameters:
        lat: latitude in degrees
        lon: longitude in degrees

    Return:
        gps coordinate string in flockctrl format
    """
    lat_sign = "N" if lat >= 0 else "S"
    lon_sign = "E" if lon >= 0 else "W"

    return f"{lat_sign}{lat:.7f} {lon_sign}{lon:.7f}"
