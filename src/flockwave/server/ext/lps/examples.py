from copy import deepcopy
from typing import Any

from trio import sleep

from .model import LocalPositioningSystem, LocalPositioningSystemType

__all__ = ("DummyLocalPositioningSystem",)


class DummyLocalPositioningSystem(LocalPositioningSystem):
    """Dummy local positioning system (LPS) that does nothing.

    This LPS instance is mostly for illustrative and testing purposes.
    """

    _configuration: dict[str, Any]
    """The current configuration of the LPS."""

    def __init__(self) -> None:
        super().__init__()
        self._configuration = {}

    async def calibrate(self) -> None:
        await sleep(3)

    async def get_configuration(self) -> dict[str, Any]:
        return deepcopy(self._configuration)

    async def _configure_inner(self, cfg: dict[str, Any]) -> None:
        self._configuration = deepcopy(cfg)


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
