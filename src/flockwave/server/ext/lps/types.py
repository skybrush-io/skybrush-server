from __future__ import annotations

from typing import TYPE_CHECKING, Any, ContextManager, Protocol

if TYPE_CHECKING:
    from flockwave.server.model.messages import FlockwaveResponse

    from .model import LocalPositioningSystem, LocalPositioningSystemType

__all__ = ("LocalPositioningSystemsExtensionAPI",)


class LocalPositioningSystemsExtensionAPI(Protocol):
    """Interface specification of the API exposed by the `lps` extension."""

    def create_and_use_lps(
        self, type: str, id: str | None = None
    ) -> ContextManager[LocalPositioningSystem[Any]]: ...

    def find_lps_by_id(
        self, id: str, response: FlockwaveResponse | None = None
    ) -> LocalPositioningSystem[Any] | None: ...

    def find_lps_type_by_id(
        self, id: str, response: FlockwaveResponse | None = None
    ) -> LocalPositioningSystemType[Any] | None: ...

    def use_lps_type(
        self, id: str, type: LocalPositioningSystemType[Any]
    ) -> ContextManager[LocalPositioningSystemType[Any]]: ...
