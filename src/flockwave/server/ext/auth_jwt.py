"""Extension that implements JWT token based authentication for the Skybrush
server.
"""

from jwt import decode
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuedAtError,
    InvalidIssuerError,
    InvalidTokenError,
)
from trio import sleep_forever

from flockwave.server.model.authentication import (
    AuthenticationMethod,
    AuthenticationResult,
)


class JWTAuthentication(AuthenticationMethod):
    def __init__(self, algorithms, secret, issuer=None, audience=None):
        """Constructor.

        Parameters:
            secret: the shared secret to use with HMAC-SHA based algorithms.
            algorithms: the list of JWT algorithms that will be accepted. Must
                be specified explicitly.
            issuer: the issuer that we expect in valid JWT tokens. `None` means
                that no validation will be performed on the issuer of the token
            audience: the audience that we expect in valid JWT tokens. `None`
                means that no validation will be performed on the audience of
                the token. You may specify multiple audiences in a list.
        """
        self._algorithms = algorithms
        self._audience = audience
        self._issuer = issuer
        self._secret = secret

    @property
    def id(self):
        return "jwt"

    def authenticate(self, client, data):
        params = {"issuer": self._issuer, "algorithms": self._algorithms}

        if self._audience is not None:
            params["audience"] = self._audience

        try:
            decoded = decode(data, self._secret, **params)
        except ExpiredSignatureError:
            return AuthenticationResult.failure(reason="JWT token expired")
        except InvalidAudienceError:
            return AuthenticationResult.failure(
                reason="JWT token audience does not match the expected one(s)"
            )
        except InvalidIssuedAtError:
            return AuthenticationResult.failure(
                reason="Invalid issue date in JWT token"
            )
        except InvalidIssuerError:
            return AuthenticationResult.failure(
                reason="JWT token issuer does not match the expected one(s)"
            )
        except InvalidTokenError:
            return AuthenticationResult.failure(reason="Invalid JWT token")

        username = decoded.get("sub")
        if not username:
            return AuthenticationResult.failure("No subject in JWT token")

        return AuthenticationResult.success(username)


async def run(app, configuration):
    secret = configuration.get("secret")
    if secret is None:
        raise ValueError("JWT shared secret must be specified")

    auth = JWTAuthentication(
        ["HS256"],
        secret,
        audience=configuration.get("audience"),
        issuer=configuration.get("issuer"),
    )
    with app.import_api("auth").use(auth):
        await sleep_forever()


dependencies = ("auth",)
