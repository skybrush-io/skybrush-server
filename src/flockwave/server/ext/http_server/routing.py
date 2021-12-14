"""Simple routing middleware for the HTTP server extension."""

from bisect import insort_left
from dataclasses import dataclass
from functools import partial, total_ordering
from typing import Any, Callable, FrozenSet, Iterable, Optional


__all__ = ("RoutingMiddleware",)


@dataclass(order=False)
@total_ordering
class Route:
    """Data class holding the details of a single route in the routing
    middleware.
    """

    app: Any
    scopes: Optional[FrozenSet[str]] = None
    path: Optional[str] = None
    priority: int = 0

    async def handle(self, scope, receive, send):
        return await self.app(scope, receive, send)

    def matches(self, scope):
        """Returns whether the route object matches (i.e. should handle)
        the given scope.
        """
        if self.scopes is not None and scope["type"] not in self.scopes:
            return False
        if self.path is not None:
            path = scope.get("path")
            if path is None or not path.startswith(self.path):
                return False
        return True

    def __lt__(self, other):
        return self.priority > other.priority


class RoutingMiddleware:
    """Simple routing middleware that acts as a top-level ASGI web application
    and forwards incoming requests to the appropriate sub-application.

    The middleware allows sub-applications to register themselves on a
    combination of a given protocol (scope type in ASGI lingo) and path.
    Incoming connections targeting a given path with a given protocol will
    be forwarded to the appropriate sub-application.

    This makes it possible for other extensions to register themselves as
    HTTP or WebSocket handlers at different paths. For instance, the Socket.IO
    extension uses this middleware to register itself for incoming HTTP and
    WebSocket requests at the designated Socket.IO path (`/socket.io`).
    """

    def __init__(self):
        """Constructor."""
        self._routes = [Route(handle_lifespan_scope, scopes=frozenset({"lifespan"}))]

    async def __call__(self, scope, receive, send):
        """Entry point for incoming requests according to the ASGI
        specification.
        """
        for route in self._routes:
            if route.matches(scope):
                return await route.handle(scope, receive, send)
        else:
            return await default_handler(scope, receive, send)

    def add(
        self,
        app,
        scopes: Optional[Iterable[str]] = None,
        path: Optional[str] = None,
        priority: int = 0,
    ) -> Callable[[], None]:
        """Mounts a new application for the given scopes at the given path.

        Parameters:
            app: the ASGI application to mount
            scopes: the ASGI scopes that the application should handle;
                `None` means to handle all scopes. The value should be an
                iterable of ASGI scopes; e.g., `http` or `websocket`.
            path: the path prefix that the application should respond to.
                `None` means to respond to all paths.
            priority: the priority of the rule. When there are multiple routes
                with the same priority, the ones added later take precedence
                over the ones added earlier.

        Returns:
            a callable that should be invoked to unmount the application
        """
        if path is not None and not path.endswith("/"):
            path = path + "/"

        route = Route(
            app,
            scopes=frozenset(scopes) if scopes is not None else None,
            path=path,
            priority=priority,
        )
        insort_left(self._routes, route)

        return partial(self._remove, route)

    def _remove(self, route: Route) -> None:
        """Removes the given route from the router."""
        self._routes.remove(route)


async def handle_lifespan_scope(scope, receive, send):
    """ASGI handler function for the events of the lifespan scope."""
    event = await receive()
    if event["type"] == "lifespan.startup":
        await send({"type": "lifespan.startup.complete"})
    elif event["type"] == "lifespan.shutdown":
        await send({"type": "lifespan.shutdown.complete"})


async def default_handler(scope, receive, send):
    """Default ASGI handler function that simply returns an HTTP 404
    error for requests that are not matched by any other handler.
    """
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"Content-Type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"Not Found"})
