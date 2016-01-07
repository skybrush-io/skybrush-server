"""Logger object for the Flockwave server."""

import logging

__all__ = ("log", )

log = logging.getLogger(__name__.rpartition(".")[0])
