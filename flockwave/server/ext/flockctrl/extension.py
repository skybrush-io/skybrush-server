"""Flockwave server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from ..base import ExtensionBase

__all__ = ("construct", )


class FlockCtrlDronesExtension(ExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def load(self, app, configuration, logger):
        """Loads the extension."""
        pass

construct = FlockCtrlDronesExtension
