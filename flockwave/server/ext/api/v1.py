"""Version 1 of the Flockwave server web API."""

from argparse import Namespace
from flask import Blueprint, jsonify
from flockwave.server.authentication import http_authentication, \
    jwt_authentication

__all__ = ("load", )


blueprint = Blueprint("api_v1", __name__, static_folder="static")


def load(app, configuration, logger):
    """Loads the extension."""
    server = app.import_api("http_server").wsgi_app
    server.register_blueprint(
        blueprint,
        url_prefix=configuration.get("route", "/api/v1")
    )


@blueprint.route("/tokens", methods=["GET", "POST"])
@http_authentication.login_required
def get_authentication_token():
    """Returns a JSON response containing an authentication token for the
    currently authenticated user. Requires login.
    """
    identity = Namespace()
    identity.id = http_authentication.username()
    token = jwt_authentication.jwt_encode_callback(identity)
    return jsonify(access_token=token)


dependencies = ("http_server", )
