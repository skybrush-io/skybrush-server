from errno import EADDRINUSE
from trio import serve_tcp

__all__ = ("serve_tcp_and_log_errors",)


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
