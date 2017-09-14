"""Model classes related to a single client connected to the server."""

from __future__ import absolute_import

__all__ = ("Client", )


class Client(object):
    """A single client connected to the Flockwave server."""

    def __init__(self, id):
        """Constructor.

        Parameters:
            id (str): a unique identifier for the client
        """
        self._id = id

    @property
    def id(self):
        """A unique identifier for the client, assigned at construction
        time.
        """
        return self._id
