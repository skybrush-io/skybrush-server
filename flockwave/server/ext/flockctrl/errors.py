"""Error classes specific to the FlockCtrl extension."""

__all__ = ("ParseError", )


class ParseError(RuntimeError):
    """Error thrown when the parser failed to parse a FlockCtrl packet."""

    pass
