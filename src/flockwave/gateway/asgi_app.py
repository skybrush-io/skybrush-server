"""ASGI web application for the gateway server."""

from argparse import Namespace
from functools import partial, wraps

from quart import abort, redirect, request
from quart_trio import QuartTrio

from .logger import log as base_log

PACKAGE_NAME = __name__.rpartition(".")[0]

log = base_log.getChild("asgi_app")

app = QuartTrio(PACKAGE_NAME)
api = Namespace()


def update_api(app):
    api.get_root_redirect_url = partial(app.config.get, "ROOT_REDIRECTS_TO")
    api.get_public_url_of_worker = app.get_public_url_of_worker
    api.request_worker = app.worker_manager.request_worker
    api.validate_jwt_token = app.validate_jwt_token


def use_fake_token(func):
    @wraps(func)
    async def handler(*args, **kwds):
        token = {"sub": "foo", "name": "bar"}
        return await func(token, *args, **kwds)

    return handler


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
    url = api.get_root_redirect_url()
    if url:
        return redirect(url)
    else:
        abort(404)


@app.route("/api/operations/start-worker", methods=["POST"])
@use_jwt_token
async def start_worker(token):
    user_id = token.get("sub")
    username = token.get("name")

    if not user_id or not username:
        return "Required information missing from token", 400

    try:
        index = await api.request_worker(id=user_id, name=username)
        return {"result": {"url": api.get_public_url_of_worker(index)}}
    except Exception as ex:
        log.exception(ex)
        return {"error": str(ex) or str(type(ex).__name__)}
