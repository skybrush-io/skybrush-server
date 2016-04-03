"""Utility functions that do not fit elsewhere."""

__all__ = ("itersubclasses", )


def itersubclasses(cls):
    """Iterates over all the subclasses of the given class in a depth-first
    manner.

    Parameters:
        cls (type): the (new-style) Python class whose subclasses we are
            iterating over

    Yields:
        type: the subclasses of the given class in DFS order, including
            the class itself.
    """
    queue = [cls]
    while queue:
        cls = queue.pop()
        yield cls
        queue.extend(cls.__subclasses__())
