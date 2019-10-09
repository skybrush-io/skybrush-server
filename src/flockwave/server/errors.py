"""Common exception classes used in many places throughout the server."""

__all__ = ("CommandInvocationError", "FlockwaveError", "NotSupportedError")


class FlockwaveError(RuntimeError):
    """Base class for all Flockwave-related errors."""

    pass


class CommandInvocationError(FlockwaveError):
    """Exception class that signals that the user tried to call some command
    of a remote UAV but failed to parameterize the command properly.
    """

    def __init__(self, message=None):
        """Constructor.

        Parameters:
            message (Optional[str]): the error message
        """
        message = message or "Command invocation error"
        super(CommandInvocationError, self).__init__(message)


class NotSupportedError(FlockwaveError):
    """Exception thrown by operations that are not supported and there are
    no plans to support them.

    This exception should be thrown instead of NotImplementedError_ if we
    know that the operation is not likely to be implemented in the future.
    """

    def __init__(self, message=None):
        """Constructor.

        Parameters:
            message (Optional[str]): the error message
        """
        message = message or "Operation not supported"
        super(NotSupportedError, self).__init__(message)
