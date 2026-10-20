"""Smoke tests that import every module of the server, the gateway and the proxy.

Most modules are not covered by any other test, so a module that fails to
import on a newer Python version (e.g., because it relies on a standard library
API that was removed) would otherwise go unnoticed.
"""

import importlib
import pkgutil
import warnings
from pathlib import Path

import pytest

import flockwave.gateway
import flockwave.proxy
import flockwave.server

ROOT_PACKAGES = (flockwave.server, flockwave.gateway, flockwave.proxy)
"""Root packages whose modules are imported by the tests in this file."""

SOURCE_ROOT = Path(flockwave.server.__file__).resolve().parent.parent
"""Root folder of the ``flockwave`` namespace package in this repository.
Deprecation warnings are reported only if they are triggered from here.
"""


def _find_all_modules() -> list[str]:
    """Returns the fully qualified names of all modules in the root packages."""
    result: list[str] = []
    for package in ROOT_PACKAGES:
        result.append(package.__name__)
        result.extend(
            info.name
            for info in pkgutil.walk_packages(
                package.__path__,
                package.__name__ + ".",
                # Packages that fail to import are still yielded by
                # walk_packages() so the corresponding test will fail; their
                # submodules cannot be discovered, though
                onerror=lambda _name: None,
            )
        )
    return sorted(result)


def _is_from_source_tree(filename: str) -> bool:
    try:
        return Path(filename).resolve().is_relative_to(SOURCE_ROOT)
    except OSError:
        return False


@pytest.mark.parametrize("name", _find_all_modules())
def test_module_can_be_imported(name: str):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(name)

    deprecations = [
        f"{w.filename}:{w.lineno}: {w.category.__name__}: {w.message}"
        for w in caught
        if issubclass(
            w.category, (DeprecationWarning, PendingDeprecationWarning, SyntaxWarning)
        )
        and _is_from_source_tree(w.filename)
    ]
    assert not deprecations, "\n".join(deprecations)
