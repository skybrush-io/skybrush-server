from __future__ import annotations

from typing import TYPE_CHECKING, ContextManager, Protocol

if TYPE_CHECKING:
    from .model import Beacon

__all__ = ("BeaconExtensionAPI",)


class BeaconExtensionAPI(Protocol):
    """Interface specification of the API exposed by the `beacon` extension."""

    def find_by_id(self, id: str) -> Beacon | None: ...
    def use(self, beacon_id: str) -> ContextManager[Beacon]: ...
