"""A registry that contains information about all the clocks and timers that
the server knows.
"""

__all__ = ("RTKPresetRegistry",)

from contextlib import contextmanager
from typing import Iterator, Optional

from flockwave.server.registries.base import RegistryBase

from .preset import RTKConfigurationPreset


class RTKPresetRegistry(RegistryBase[RTKConfigurationPreset]):
    """Registry that contains information about the RTK presets registered in
    the server.
    """

    def add(self, preset: RTKConfigurationPreset) -> None:
        """Registers an RTK preset in the registry.

        This function is a no-op if the preset is already registered.

        Parameters:
            clock: the clock to register

        Throws:
            KeyError: if the ID of the preset is already taken by a different preset
        """
        old_preset = self._entries.get(preset.id, None)
        if old_preset is not None and old_preset != preset:
            raise KeyError(f"Preset ID already taken: {preset.id}")
        self._entries[preset.id] = preset

    def remove(
        self, preset: RTKConfigurationPreset
    ) -> Optional[RTKConfigurationPreset]:
        """Removes the given preset from the registry.

        This function is a no-op if the preset is not registered.

        Parameters:
            preset: the preset to deregister

        Returns:
            the preset that was deregistered, or ``None`` if the preset was not
                registered
        """
        return self.remove_by_id(preset.id)

    def remove_by_id(self, preset_id: str) -> Optional[RTKConfigurationPreset]:
        """Removes the preset with the given ID from the registry.

        This function is a no-op if no preset is registered with the given ID.

        Parameters:
            preset_id: the ID of the preset to deregister

        Returns:
            the preset that was deregistered, or ``None`` if the preset was not
            registered
        """
        return self._entries.pop(preset_id, None)

    @contextmanager
    def use(self, preset: RTKConfigurationPreset) -> Iterator[RTKConfigurationPreset]:
        """Temporarily adds a new preset, hands control back to the caller in a
        context, and then removes the preset when the caller exits the context.

        Parameters:
            preset: the preset to add

        Yields:
            RTKConfigurationPreset: the preset object that was added
        """
        self.add(preset)
        try:
            yield preset
        finally:
            self.remove(preset)
