from trio import sleep
from typing import Any

from .model import LocalPositioningSystem, LocalPositioningSystemType

__all__ = ("DummyLocalPositioningSystem",)


class DummyLocalPositioningSystem(LocalPositioningSystem):
    """Dummy local positioning system (LPS) that does nothing.

    This LPS instance is mostly for illustrative and testing purposes.
    """

    async def calibrate(self) -> None:
        await sleep(3)


class DummyLocalPositioningSystemType(
    LocalPositioningSystemType[DummyLocalPositioningSystem]
):
    """Example local positioning system (LPS) type that does nothing.

    This LPS type is mostly for illustrative and testing purposes.
    """

    @property
    def description(self) -> str:
        return "Local positioning system example that does nothing."

    @property
    def name(self) -> str:
        return "Dummy LPS"

    def create(self) -> DummyLocalPositioningSystem:
        return DummyLocalPositioningSystem()

    def get_configuration_schema(self) -> dict[str, Any]:
        return {}
