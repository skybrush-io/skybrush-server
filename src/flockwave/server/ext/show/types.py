from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from flockwave.server.tasks.led_lights import LightConfiguration

    from .clock import ShowClock
    from .config import DroneShowConfiguration
    from .metadata import ShowMetadata

__all__ = ("ShowExtensionAPI",)


class ShowExtensionAPI(Protocol):
    """Interface specification of the API exposed by the `show` extension."""

    def get_clock(self) -> ShowClock | None: ...
    def get_configuration(self) -> DroneShowConfiguration: ...
    def get_light_configuration(self) -> LightConfiguration: ...
    def get_last_uploaded_show_metadata(self) -> ShowMetadata | None: ...
