import flockwave.server.ext.webui as webui
import os

datas = [
    (
        os.path.join(os.path.dirname(webui.__file__), "static"),
        "flockwave/server/ext/webui/static",
    ),
    (
        os.path.join(os.path.dirname(webui.__file__), "templates"),
        "flockwave/server/ext/webui/templates",
    ),
]
