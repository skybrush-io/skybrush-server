"""Helper functions shared by the rate limiter tests."""

__all__ = ("async_noop",)


async def async_noop(*args, **kwds):
    """Async function that does nothing."""
    pass
