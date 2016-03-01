"""Common exception classes used in many places throughout the server."""

__all__ = ("NotSupportedError", )


class NotSupportedError(RuntimeError):
    """Exception thrown by operations that are not supported and there are
    no plans to support them.

    This exception should be thrown instead of NotImplementedError_ if we
    know that the operation is not likely to be implemented in the future.
    """

    def __init__(self, message=None):
        message = message or "Operation not supported"
        super(NotSupportedError, self).__init__(message)
