"""Type aliases used in multiple places throughout the server."""

from typing import Any, Callable

__all__ = ("Disposer",)


#: Type specification for disposer functions that can be called with no arguments
#: to get rid of something registered earlier
Disposer = Callable[[], Any]
