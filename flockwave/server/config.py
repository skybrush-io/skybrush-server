"""Default configuration for the Flockwave server."""

SECRET_KEY = b'\xa6\xd6\xd3a\xfd\xd9\x08R\xd2U\x05\x10'\
    b'\xbf\x8c2\t\t\x94\xb5R\x06z\xe5\xef'

EXTENSIONS = {
    "debugging": {
        "route": "/"
    },
    "fake_uavs": {
        "count": 3,
        "delay": 2,
        "id_format": "FAKE-{0:02}",
        "center": {
            "lat": 47.473360,
            "lon": 19.062159,
            "altRel": 20
        },
        "radius": 50,
        "time_of_single_cycle": 10
    }
}
