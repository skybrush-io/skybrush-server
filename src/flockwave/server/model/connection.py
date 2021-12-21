"""Connection-related model objects."""

from flockwave.spec.schema import get_complex_object_schema, get_enum_from_schema
from typing import Optional

from .metamagic import ModelMeta
from .mixins import TimestampLike, TimestampMixin

__all__ = ("ConnectionInfo", "ConnectionPurpose", "ConnectionStatus")


ConnectionPurpose = get_enum_from_schema("connectionPurpose", "ConnectionPurpose")
ConnectionStatus = get_enum_from_schema("connectionStatus", "ConnectionStatus")


class ConnectionInfo(TimestampMixin, metaclass=ModelMeta):
    """Class representing the status information available about a single
    connection.
    """

    class __meta__:
        schema = get_complex_object_schema("connectionInfo")

    _STATUS_MAPPING = {
        "CONNECTED": ConnectionStatus.connected,
        "CONNECTING": ConnectionStatus.connecting,
        "DISCONNECTED": ConnectionStatus.disconnected,
        "DISCONNECTING": ConnectionStatus.disconnecting,
    }

    id: Optional[str]

    def __init__(
        self, id: Optional[str] = None, timestamp: Optional[TimestampLike] = None
    ):
        """Constructor.

        Parameters:
            id (str or None): ID of the connection
            timestamp (datetime or None): time when the last packet was
                received from the connection, or if it is not available,
                the time when the conncetion changed status the last time.
                ``None`` means to use the current date and time.
        """
        TimestampMixin.__init__(self, timestamp)
        self.id = id
        self.purpose = ConnectionPurpose.other
        self.status = ConnectionStatus.unknown

    def update_status_from(self, connection):
        """Updates the status member of this object from the status of the
        given connection.

        Parameters:
            connection (Connection): the connection from which the status
                is to be updated
        """
        status = connection.state.name if connection is not None else None
        self.status = self._STATUS_MAPPING.get(status, status)
