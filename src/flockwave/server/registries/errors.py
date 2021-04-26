"""Error classes specific to registries."""

from flockwave.server.errors import FlockwaveError

__all__ = ("RegistryError", "RegistryFull")


class RegistryError(FlockwaveError):
    """Base class for all error classes related to registries."""

    pass


class RegistryFull(RegistryError):
    """Error thrown when a new object cannot be registered in a registry due to
    some internal size limit.
    """

    pass
