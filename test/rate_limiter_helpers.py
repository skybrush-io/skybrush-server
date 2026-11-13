"""Helper functions shared by the rate limiter tests."""

__all__ = ("create_message", "noop_dispatcher")


def create_message(items):
    """Message factory that returns the requested items as a tuple."""
    return tuple(items)


async def noop_dispatcher(message):
    """Dispatcher that discards every message."""
