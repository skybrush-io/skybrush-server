from __future__ import annotations

from collections.abc import Callable
from typing import ContextManager, Protocol, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import UPnPServiceRegistry, URIOrCallableReturningURI

__all__ = ("SSDPExtensionAPI",)


class SSDPExtensionAPIDict(TypedDict):
    register_service: Callable[[str, URIOrCallableReturningURI], None] | None
    registry: UPnPServiceRegistry | None
    unregister_service: Callable[[str], URIOrCallableReturningURI | None] | None
    use_service: Callable[[str, URIOrCallableReturningURI], ContextManager[None]] | None


class SSDPExtensionAPI(Protocol):
    """Interface specification for the methods exposed by the ``ssdp``
    extension.
    """

    register_service: Callable[[str, URIOrCallableReturningURI], None]
    registry: UPnPServiceRegistry
    unregister_service: Callable[[str], URIOrCallableReturningURI | None]
    use_service: Callable[[str, URIOrCallableReturningURI], ContextManager[None]]
