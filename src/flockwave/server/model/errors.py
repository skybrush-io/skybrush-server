"""Error classes specific to the Flockwave model."""

from builtins import str

from flockwave.server.errors import FlockwaveError

__all__ = ("ClientNotSubscribedError", "NoSuchPathError")


class ClientNotSubscribedError(FlockwaveError):
    """Error thrown when a client attempts to unsubscribe from a part of the
    device tree that it is not subscribed to.
    """

    def __init__(self, client, path):
        """Constructor.

        Parameters:
            client (Client): the client that attempted to unsubscribe
            path (DeviceTreePath): the path that the client attempted to
                unsubscribe from
        """
        super(ClientNotSubscribedError, self).__init__(str(client))
        self.client = client
        self.path = path


class NoSuchPathError(FlockwaveError):
    """Error thrown when the device tree failed to resolve a device tree
    path to a corresponding node.
    """

    def __init__(self, path):
        """Constructor.

        Parameters:
            path (DeviceTreePath): the path that could not be resolved into
                a node
        """
        super(NoSuchPathError, self).__init__(str(path))
        self.path = path
