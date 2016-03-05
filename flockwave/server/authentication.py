"""Configuration of authentication-related objects in a Flockwave server
application.
"""

from flask import _request_ctx_stack
from flask.ext.httpauth import HTTPBasicAuth
from flask.ext.jwt import JWT, jwt_required
from functools import wraps
from jwt import InvalidTokenError
from .logger import log as base_log

__all__ = ("http_authentication", "jwt_authentication", "jwt_required")

log = base_log.getChild("authentication")

http_authentication = HTTPBasicAuth()
jwt_authentication = JWT()


@http_authentication.verify_password
def verify_password(username, password):
    """Returns whether the given username-password pair is valid.

    Currently this is unimplemented; we accept every single username-password
    pair without validation. This method has to be replaced with a proper
    implementation using ``werkzeug.security.check_password_hash()``.

    Parameters:
        username (str): the name of the user wishing to authenticate
        password (str): the password that the user has entered

    Returns:
        bool: whether the username-password pair is valid
    """
    if not username:
        return False

    log.warn("Username-password validation is not implemented yet")
    return True


def _get_jwt_identity():
    """Returns the current user identity as dictated by the JWT token
    passed in the request header.

    Returns:
        object: the JWT identity from the token or ``None`` if there was
            no token in the request headers
    """
    stack_top = _request_ctx_stack.top
    stack_top.current_identity = _get_jwt_identity_inner()
    return stack_top.current_identity


def _get_jwt_identity_inner():
    """Actual implementation of ``_get_jwt_identity()`` that is invoked from
    ``_get_jwt_identity()``.

    Returns:
        object: the JWT identity from the token or ``None`` if there was
            no token in the request headers
    """
    token = jwt_authentication.request_callback()
    if token is None:
        return None

    try:
        payload = jwt_authentication.jwt_decode_callback(token)
    except InvalidTokenError:
        return None

    identity = jwt_authentication.identity_callback(payload)
    return identity


def jwt_optional():
    """Decorator that decorates a function in a way that ensures that the
    ``current_identity`` proxy of ``flask.ext.jwt`` is set to the current
    JWT identity or ``None`` if the user has not authenticated via JWT,
    but does _not_ fail the request if there is no valid token.
    """
    def wrapper(func):
        @wraps(func)
        def wrapped(*args, **kwds):
            _get_jwt_identity()
            return func(*args, **kwds)
        return wrapped
    return wrapper
