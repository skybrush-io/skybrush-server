from __future__ import annotations

from base64 import b64encode, b64decode
from dataclasses import dataclass
from re import match, sub
from typing import Callable, ClassVar, TYPE_CHECKING
from wrapt import ObjectProxy

from .errors import InvalidSigningKeyError

if TYPE_CHECKING:
    from flockwave.protocols.mavlink.types import (
        MAVLinkInterface,
        MAVLinkSigningInterface,
    )

__all__ = ("MAVLinkSigningConfiguration", "SignatureTimestampSynchronizer")


@dataclass(frozen=True)
class MAVLinkSigningConfiguration:
    """Configuration of MAVLink signing on a single MAVLink network."""

    enabled: bool = False
    """Whether signing is enabled in general."""

    key: bytes = b""
    """Signing key; must be a bytes object of length 32."""

    sign_outbound: bool = True
    """Whether to sign outbound MAVLink messages."""

    allow_unsigned: bool = False
    """Whether to accept unsigned incoming MAVLink messages."""

    DISABLED: ClassVar[MAVLinkSigningConfiguration]
    """Special instance to denote the case when MAVLink signing is disabled."""

    @classmethod
    def from_json(cls, obj):
        """Constructs a MAVLink signing configuration from its JSON
        representation.
        """
        enabled = bool(obj.get("enabled"))
        sign_outbound = bool(obj.get("sign_outbound", True))
        allow_unsigned = bool(obj.get("allow_unsigned"))

        key_spec = str(obj.get("key", ""))

        # Check whether key_spec is hexadecimal and has the correct length
        if isinstance(key_spec, bytes):
            key = key_spec
        else:
            key_spec_stripped = sub("[- :]", "", key_spec.strip())
            try:
                if len(key_spec_stripped) == 64 and match(
                    "^[0-9a-fA-F]*$", key_spec_stripped
                ):
                    key = bytes.fromhex(key_spec_stripped)
                else:
                    key = b64decode(key_spec_stripped)
            except Exception:
                if enabled:
                    raise InvalidSigningKeyError(
                        f"Signing key {key_spec!r} is not a valid base64-encoded "
                        f"or hexadecimal string"
                    ) from None
                else:
                    key = b""

        if len(key) != 32:
            if enabled:
                raise InvalidSigningKeyError(
                    f"MAVLink signing keys must be 32 bytes long, got {len(key)}"
                )
            else:
                key = b""

        return cls(
            enabled=enabled,
            key=key,
            sign_outbound=sign_outbound,
            allow_unsigned=allow_unsigned,
        )

    @property
    def json(self):
        """Returns the JSON representation of the signing configuration."""
        return {
            "enabled": bool(self.enabled),
            "key": b64encode(self.key).decode("ascii") if self.key else "",
            "sign_outbound": bool(self.sign_outbound),
            "allow_unsigned": bool(self.allow_unsigned),
        }


MAVLinkSigningConfiguration.DISABLED = MAVLinkSigningConfiguration()


class SignatureTimestampSynchronizer:
    """Helper object to synchronize MAVLink signing timestamps between
    multiple MAVLinkSigning_ objects.

    This object keeps track of a common timestamp that is only ever allowed to
    move forward. MAVLinkSigning_ objects can be wrapped by a special-purpose
    object proxy instance that overrides the `timestamp` property with a getter
    and setter that returns and updates the common timestamp instead of the
    individual timestamp of the MAVLinkSigning_ object.
    """

    _timestamp: int
    """Common timestamp for each of the MAVLinkSigning_ objects wrapped by
    this synchronizer instance.
    """

    class TimestampProxy(ObjectProxy):
        def __init__(
            self,
            wrapped: MAVLinkSigningInterface,
            getter: Callable[[], int],
            updater: Callable[[int], None],
        ):
            self._self_getter = getter
            self._self_updater = updater
            super().__init__(wrapped)

        @property
        def timestamp(self) -> int:
            return self._self_getter()

        @timestamp.setter
        def timestamp(self, value: int) -> None:
            return self._self_updater(value)

    def __init__(self, initial: int = 0):
        """Constructor.

        Arguments:
            initial: the initial timestamp to use by the synchronizer
        """
        self._timestamp = initial

    def _get_timestamp(self) -> int:
        """Returns the timestamp in this object."""
        return self._timestamp

    def _update_timestamp(self, value: int) -> None:
        """Updates the timestamp in this object if it is smaller than the given
        value. Leaves the timestamp intact otherwise.
        """
        if self._timestamp < value:
            self._timestamp = value

    @property
    def timestamp(self) -> int:
        return self._get_timestamp()

    def patch(self, mavlink: MAVLinkInterface) -> None:
        """Patches a MAVLink object to use a synchronized signature timestamp."""
        mavlink.signing = self.wrap(mavlink.signing)

    def wrap(self, signing_state) -> MAVLinkSigningInterface:
        """Wraps an existing MAVLinkSigning_ object with an object proxy that
        overrides the timestamp to correspond to the common timestamp in this
        timestamp synchronizer class.
        """
        self._update_timestamp(signing_state.timestamp)
        return self.TimestampProxy(
            signing_state, self._get_timestamp, self._update_timestamp
        )
