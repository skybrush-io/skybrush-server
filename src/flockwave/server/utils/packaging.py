"""Utility functions related to distributing the server as a packaged,
preferably single-file application.
"""

from functools import lru_cache

import sys

__all__ = ("is_oxidized", "is_packaged", "is_packaged_with_pyinstaller")


@lru_cache(maxsize=None)
def is_packaged() -> bool:
    """Returns whether the application is packaged."""
    return is_oxidized() or is_packaged_with_pyinstaller()


def is_oxidized() -> bool:
    """Returns whether the application is packaged with PyOxidizer."""
    return bool(getattr(sys, "oxidized", False))


def is_packaged_with_pyinstaller() -> bool:
    """Returns whether the application is packaged with PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
