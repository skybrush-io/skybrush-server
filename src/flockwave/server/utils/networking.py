import platform
from collections.abc import Callable
from errno import EADDRINUSE

from trio import serve_tcp

__all__ = ("serve_tcp_and_log_errors",)


def _is_macos() -> bool:
    return platform.system() == "Darwin"


KNOWN_APPS: dict[int, list[tuple[Callable[[], bool], str]]] = {
    5000: [(_is_macos, "AirPlay receiver")],
}


def get_known_apps_for_port(port: int) -> list[str]:
    """Return a list of known applications that may use the given port.

    Args:
        port: The port number to check.

    Returns:
        A list of application names that may use the given port.
    """
    apps: list[str] = []
    for predicate, app in KNOWN_APPS.get(port, []):
        try:
            if predicate():
                apps.append(app)
        except Exception:
            pass
    return apps


async def serve_tcp_and_log_errors(handler, port, *, log, **kwds):
    """Wrapper of Trio's `serve_tcp()` that handles and logs some common
    errors.
    """
    try:
        return await serve_tcp(handler, port, **kwds)
    except OSError as ex:
        if ex.errno == EADDRINUSE:
            log.error(f"Port {port} is already in use", extra={"telemetry": "ignore"})
        else:
            raise
