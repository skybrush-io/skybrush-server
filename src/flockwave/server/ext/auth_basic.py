"""Extension that implements basic username-password based authentication
for the Flockwave server.

The authentication messages themselves are easy to decipher if they are not
sent on an encrypted channel. Make sure that the channels provided by the
server are secure if you are using this extension for authentication.
"""

from base64 import b64decode
from flockwave.server.model.authentication import (
    AuthenticationMethod,
    AuthenticationResult,
)


class BasicAuthentication(AuthenticationMethod):
    @property
    def id(self):
        return "basic"

    def authenticate(self, client, data):
        try:
            decoded = b64decode(data.encode("ascii")).decode("utf-8")
        except Exception:
            return AuthenticationResult.failure()

        user, sep, password = decoded.partition(":")

        if not user or not sep or not password:
            return AuthenticationResult.failure()
        if user == "user@domain.xyz" and password == "password":
            return AuthenticationResult.success(user)
        else:
            return AuthenticationResult.failure()


BasicAuthentication = BasicAuthentication()


def load(app):
    app.import_api("auth").register(BasicAuthentication)


def unload(app):
    app.import_api("auth").unregister(BasicAuthentication)


dependencies = ("auth",)
