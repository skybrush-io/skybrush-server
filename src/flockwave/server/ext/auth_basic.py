"""Extension that implements basic username-password based authentication
for the Skybrush server.

The authentication messages themselves are easy to decipher if they are not
sent on an encrypted channel. Make sure that the channels provided by the
server are secure if you are using this extension for authentication.
"""

from base64 import b64decode
from enum import Enum
from pathlib import Path
from trio import sleep_forever
from typing import Callable, Dict, List, Mapping, Optional, Union

from flockwave.server.model.authentication import (
    AuthenticationMethod,
    AuthenticationResult,
)


#: Type specification for a function that compares a password with its hash
HashComparator = Callable[[str, str], bool]

#: Type specification for a password validator function
PasswordValidator = Callable[[str, str], bool]

#: Type specification for password validator specification objects
PasswordValidatorSpecification = Dict[str, str]


class PasswordDataSourceType(Enum):
    """Enumeration describing the possible password data sources that the
    extension supports.
    """

    HTPASSWD = ("htpasswd", "Apache htpasswd")
    SINGLE = ("single", "Username-password pair")

    description: str

    def __init__(self, id: str, description: str):
        self.id = id
        self.description = description


# ############################################################################


def create_dict_validator(
    passwords: Mapping[str, str], compare: Optional[HashComparator] = None
) -> PasswordValidator:
    """Password validator factory that validates passwords from the given
    dictionary.

    Parameters:
        passwords: dictionary mapping usernames to the corresponding valid
            passwords or hashes
        compare: function that receives the password (as entered by the user)
            and its hash, and returns whether the password matches the hash.
            Defaults to strict equality

    Returns:
        an appropriate password validator function
    """

    def validator(username: str, password: str) -> bool:
        try:
            expected = passwords[username]
        except KeyError:
            return False
        return compare(password, expected) if compare else (password == expected)

    return validator


def create_htpasswd_validator(filename: Union[Path, str]) -> PasswordValidator:
    """Password validator factory that validates passwords using the given
    htpasswd file.

    Parameters:
        filename: the name of the file that contains the passwords, in htpasswd
            format

    Returns:
        an appropriate password validator function
    """
    global log

    from passlib.apache import HtpasswdFile

    ht = HtpasswdFile(str(filename))

    def validator(username: str, password: str) -> bool:
        # check_password() returns None if the username does not exist, but we
        # can only return True or False, hence the cast
        ht.load_if_changed()
        return bool(ht.check_password(username, password))

    return validator


def create_single_validator(
    expected_username: str, expected_password: str
) -> PasswordValidator:
    """Password validator factory that accepts a single username-password
    pair only, given at construction time.

    Parameters:
        expected_username: the username to accept
        expected_password: the password to accept

    Returns:
        an appropriate password validator function
    """

    def validator(username: str, password: str) -> bool:
        return username == expected_username and password == expected_password

    return validator


def reject_all(username: str, password: str) -> bool:
    """Dummy password validator that rejects all username-password pairs."""
    return False


# ############################################################################


def create_validator_from_config(
    spec: PasswordValidatorSpecification,
) -> PasswordValidator:
    """Creates a PasswordValidator_ instance from its representation in the
    configuration object of the extension.

    Parameters:
        spec: the specification in the configuration object, with keys named
            ``type`` and ``value``

    Returns:
        an appropriate PasswordValidator_ instance

    Raises:
        RuntimeError: for invalid configuration objects
        FileNotFound: when the password file is not found
    """
    type = spec.get("type")
    value = spec.get("value")

    if not isinstance(type, str) or not isinstance(value, str):
        raise RuntimeError(f"invalid password data source: {spec!r}")

    if type == PasswordDataSourceType.SINGLE.id:
        parts = value.split(None, 1)
        if len(parts) < 2:
            raise RuntimeError(
                f"missing username or password in password data source: {value!r}"
            )
        return create_single_validator(parts[0], parts[1])
    elif type == PasswordDataSourceType.HTPASSWD.id:
        if value:
            return create_htpasswd_validator(value)
        else:
            raise RuntimeError(
                "no filename was specified for Apache htpasswd data source"
            )
    else:
        raise RuntimeError(f"unknown password data source: {type!r}")


# ############################################################################


class BasicAuthentication(AuthenticationMethod):
    """Implementation of a basic username-password-based authentication method."""

    _validators: List[PasswordValidator]
    """List of registered password validators. The username-password pair is
    deemed valid if at least one of the validators accepts it.
    """

    def __init__(self):
        """Constructor."""
        self._validators = []

    def add_validator(self, validator: PasswordValidator) -> None:
        """Adds a new password validator to the list of registered password
        validators.
        """
        self._validators.append(validator)

    def authenticate(self, client, data):
        try:
            decoded = b64decode(data.encode("ascii")).decode("utf-8")
        except Exception:
            return AuthenticationResult.failure()

        user, sep, password = decoded.partition(":")

        if not user or not sep or not password:
            return AuthenticationResult.failure()
        if any(valid(user, password) for valid in self._validators):
            return AuthenticationResult.success(user)
        else:
            return AuthenticationResult.failure()

    @property
    def id(self):
        return "basic"


async def run(app, configuration, logger):
    auth = BasicAuthentication()
    sources = configuration.get("sources", ())

    if not hasattr(sources, "__iter__"):
        logger.error(
            "Invalid configuration; password data sources must be stored in an array"
        )

    for spec in sources:
        validator: Optional[PasswordValidator] = None

        try:
            validator = create_validator_from_config(spec)
        except FileNotFoundError:
            filename = repr(spec.get("value"))
            logger.error(
                f"Password file not found: {filename}", extra={"telemetry": "ignore"}
            )
        except RuntimeError as ex:
            logger.error(str(ex), extra={"telemetry": "ignore"})
        except Exception:
            logger.exception("Unexpected error while creating password validator")

        if validator:
            auth.add_validator(validator)

    with app.import_api("auth").use(auth):
        await sleep_forever()


dependencies = ("auth",)
description = "Basic username-password based authentication"
schema = {
    "properties": {
        "sources": {
            "title": "Password data sources",
            "type": "array",
            "format": "table",
            "items": {
                "type": "object",
                "options": {"disable_properties": False},
                "properties": {
                    "type": {
                        "type": "string",
                        "title": "Type",
                        "description": "Type of the data source",
                        "default": PasswordDataSourceType.HTPASSWD.id,
                        "enum": [e.id for e in PasswordDataSourceType],
                        "options": {
                            "enum_titles": [
                                e.description for e in PasswordDataSourceType
                            ]
                        },
                    },
                    "value": {
                        "type": "string",
                        "title": "Value",
                        "description": (
                            "The data source itself. For Apache htpasswd files, "
                            "this must be the path to the htpasswd file. For "
                            "explicit username-password pairs, this must be the "
                            "username and the password, separated by whitespace."
                        ),
                    },
                },
            },
            "description": (
                "WARNING: do not use explicit username-password pairs as these "
                "are stored in plain text in the configuration file. Use another "
                "data source in production."
            ),
        }
    }
}
