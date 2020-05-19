"""Extension that implements basic username-password based authentication
for the Flockwave server.

The authentication messages themselves are easy to decipher if they are not
sent on an encrypted channel. Make sure that the channels provided by the
server are secure if you are using this extension for authentication.
"""

from base64 import b64decode
from pathlib import Path
from trio import sleep_forever
from typing import Callable, Dict, Optional, Union

from flockwave.server.model.authentication import (
    AuthenticationMethod,
    AuthenticationResult,
)


#: Type specification for a function that compares a password with its hash
HashComparator = Callable[[str, str], bool]

#: Type specification for a password validator function
PasswordValidator = Callable[[str, str], bool]

#: Type specification for objects that can be converted into a password validator
PasswordValidatorLike = Optional[Union[PasswordValidator, Dict[str, str], str]]


def create_dict_validator(
    passwords: Dict[str, str], compare: Optional[HashComparator] = None
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


def reject_all(username: str, password: str) -> bool:
    """Dummy password validator that rejects all username-password pairs."""
    return False


class BasicAuthentication(AuthenticationMethod):
    def __init__(self, validator: PasswordValidatorLike = None):
        """Constructor.

        Parameters:
            validator: the name of the ``htpasswd`` file holding the valid
                username-password pairs, a dictionary that maps usernames to
                passwords, or a callable that can be invoked with a username
                and a password and that must return whether the password is
                valid
        """
        self._validator = reject_all
        self.validator = validator

    def authenticate(self, client, data):
        try:
            decoded = b64decode(data.encode("ascii")).decode("utf-8")
        except Exception:
            return AuthenticationResult.failure()

        user, sep, password = decoded.partition(":")

        if not user or not sep or not password:
            return AuthenticationResult.failure()
        if self._validator(user, password):
            return AuthenticationResult.success(user)
        else:
            return AuthenticationResult.failure()

    @property
    def id(self):
        return "basic"

    @property
    def validator(self) -> PasswordValidator:
        """Returns the current password validator function."""
        return self._validator

    @validator.setter
    def validator(self, value):
        if value is None:
            value = reject_all
        elif isinstance(value, (str, Path)):
            value = create_htpasswd_validator(value)
        elif hasattr(value, "__getitem__"):
            value = create_dict_validator(value)

        self._validator = value


async def run(app, configuration, logger):
    auth = BasicAuthentication()
    validator = configuration.get("passwords")

    try:
        auth.validator = validator
    except FileNotFoundError:
        logger.error(f"Password file not found: {validator}")

    with app.import_api("auth").use(auth):
        await sleep_forever()


dependencies = ("auth",)
