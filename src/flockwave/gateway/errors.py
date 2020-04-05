"""Error classes used in the gateway server."""

__all__ = ("NoIdleWorkerError",)


class NoIdleWorkerError(RuntimeError):
    """Exception thrown when there aren't any idle workers available."""

    pass
