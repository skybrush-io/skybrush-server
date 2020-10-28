import flockwave.server.ext.debug as debug
import os

datas = [
    (
        os.path.join(os.path.dirname(debug.__file__), "static"),
        "flockwave/server/ext/debug/static"
    ),
    (
        os.path.join(os.path.dirname(debug.__file__), "templates"),
        "flockwave/server/ext/debug/templates"
    )
]
