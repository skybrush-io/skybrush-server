from dataclasses import dataclass

__all__ = ("ShowMetadata", "NO_UID")


NO_UID = b"\x00\x00\x00\x00"
"""Constant representing a "no UID" value, as a 4-byte bytes object."""


@dataclass
class ShowMetadata:
    """Class representing the metadata of a show."""

    uid: bytes = NO_UID
    """Unique identifier of the show, as a 4-byte UID."""

    drone_index: int = 0
    """Index of the drone in the show, starting from 0."""
