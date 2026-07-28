from typing import Callable, ContextManager, Iterable, Protocol, TypedDict

from flockwave.connections import IPAddressAndPort
from hypercorn.typing import ASGIFramework
from quart import Blueprint

from flockwave.server.types import Disposer

from .routing import RoutingMiddleware

__all__ = ("HTTPServerExtensionAPI",)


class MountFn(Protocol):
    def __call__(
        self,
        app: ASGIFramework | Blueprint,
        *,
        path: str,
        scopes: Iterable[str] | None = None,
        priority: int = 0,
    ) -> Disposer | None: ...


class MountedFn(Protocol):
    def __call__(
        self,
        app: ASGIFramework | Blueprint,
        *,
        path: str,
        scopes: Iterable[str] | None = None,
        priority: int = 0,
    ) -> ContextManager[None]: ...


class HTTPServerExtensionAPIDict(TypedDict):
    """Interface specification of the dict representation of the API exposed by the
    `http_server` extension.
    """

    address: IPAddressAndPort | None
    asgi_app: RoutingMiddleware | None
    mount: MountFn
    mounted: MountedFn
    propose_index_page: Callable[[str, int], Disposer]
    proposed_index_page: Callable[[str, int], ContextManager[None]]


class HTTPServerExtensionAPI(Protocol):
    """Interface specification of the API exposed by the `http_server` extension."""

    address: IPAddressAndPort | None
    asgi_app: RoutingMiddleware | None
    mount: MountFn
    mounted: MountedFn

    def propose_index_page(self, route: str, priority: int = 0) -> Disposer: ...
    def proposed_index_page(
        self, route: str, priority: int = 0
    ) -> ContextManager[None]: ...
