from __future__ import annotations

from typing import TYPE_CHECKING, ContextManager, Protocol

if TYPE_CHECKING:
    from .extension import FrontPageLink

__all__ = ("FrontendExtensionAPI",)


class FrontendExtensionAPI(Protocol):
    """Interface specification of the API exposed by the `frontend` extension."""

    def use_link_on_front_page(
        self, route: str, title: str, *, priority: int = 0
    ) -> ContextManager[FrontPageLink]: ...
