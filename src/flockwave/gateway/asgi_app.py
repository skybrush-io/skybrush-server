"""ASGI web application for the gateway server."""

from argparse import Namespace
from quart_trio import QuartTrio

from .logger import log as base_log

PACKAGE_NAME = __name__.rpartition(".")[0]

log = base_log.getChild("asgi_app")

app = QuartTrio(PACKAGE_NAME)
api = Namespace()


def update_api(app):
    api.request_worker = app.worker_manager.request_worker


@app.route("/")
async def index():
    return "Hello"


@app.route("/api/operations/start-worker", methods=["POST"])
async def start_worker():
    try:
        port = await api.request_worker()
        return {"result": port}
    except Exception as ex:
        log.exception(ex)
        return {"error": str(ex) or str(type(ex).__name__)}
