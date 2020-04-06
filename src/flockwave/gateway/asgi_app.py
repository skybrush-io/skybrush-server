"""ASGI web application for the gateway server."""

from argparse import Namespace
from functools import wraps

from quart import request
from quart_trio import QuartTrio

from .logger import log as base_log

PACKAGE_NAME = __name__.rpartition(".")[0]

log = base_log.getChild("asgi_app")

app = QuartTrio(PACKAGE_NAME)
api = Namespace()


def update_api(app):
    api.request_worker = app.worker_manager.request_worker
    api.validate_jwt_token = app.validate_jwt_token


def use_jwt_token(func):
    @wraps(func)
    async def handler(*args, **kwds):
        authorization = request.headers.get("Authorization")
        if not authorization or not authorization.startswith("Bearer "):
            return "", 401, {"WWW-Authenticate": "Bearer"}

        try:
            token = api.validate_jwt_token(authorization[7:])
        except Exception:
            return "Invalid bearer token", 403, {"WWW-Authenticate": "Bearer"}

        return await func(token, *args, **kwds)

    return handler


@app.route("/")
async def index():
    return "Hello"


@app.route("/api/operations/start-worker", methods=["POST"])
@use_jwt_token
async def start_worker(token):
    user_id = token.get("sub")
    username = token.get("name")

    if not user_id or not username:
        return "Required information missing from token", 400

    host = request.host.rpartition(":")[0]
    scheme = request.scheme

    try:
        port = await api.request_worker(id=user_id, name=username)
        return {"result": {"url": f"{scheme}://{host}:{port}"}}
    except Exception as ex:
        log.exception(ex)
        return {"error": str(ex) or str(type(ex).__name__)}
