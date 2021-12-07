from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .packaging import is_oxidized

if TYPE_CHECKING:
    from quart import Blueprint


def _get_quart_root_path_of(name: str) -> Optional[str]:
    if is_oxidized():
        # Running inside PyOxidizer, return the current folder as a dummy
        # root path for Quart
        return str(Path.cwd())
    else:
        # Running as a "normal" Python application, return None and let
        # Quart sort it out
        return None


def make_blueprint(name, import_name, *args, **kwds) -> "Blueprint":
    """Creates a Quart blueprint that takes into account whether we are running
    in a PyOxidizer-enabled distribution or not.
    """
    from quart import Blueprint

    if "root_path" not in kwds:
        kwds["root_path"] = _get_quart_root_path_of(name)

    return Blueprint(name, import_name, *args, **kwds)
