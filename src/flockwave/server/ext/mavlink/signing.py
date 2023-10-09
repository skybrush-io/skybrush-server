from __future__ import annotations

from base64 import b64encode, b64decode
from dataclasses import dataclass
from re import match, sub
from typing import ClassVar

from .errors import InvalidSigningKeyError

__all__ = ("MAVLinkSigningConfiguration",)


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
            key_spec_stripped = sub("[- :]", "", key_spec.strip()).lower()
            try:
                if len(key_spec_stripped) == 64 and match(
                    "^[0-9a-f]*$", key_spec_stripped
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
