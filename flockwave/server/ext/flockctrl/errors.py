"""Error classes specific to the FlockCtrl extension."""

__all__ = ("ParseError", )


class FlockCtrlError(RuntimeError):
    """Base class for all error classes related to the FlockCtrl
    extension.
    """

    pass


class ParseError(FlockCtrlError):
    """Error thrown when the parser failed to parse a FlockCtrl packet."""

    pass


class AddressConflictError(FlockCtrlError):
    """Error thrown when the driver receives a packet with a given UAV
    ID and a mismatching source address.
    """

    def __init__(self, uav, medium, address):
        """Constructor.

        Parameters:
            uav (FlockCtrlUAV): the UAV that the packet is addressed to,
                based on the UAV ID found in the packet
            medium (str): the communication medium on which the address is
                valid
            address (object): the source address where the packet came from
        """
        super(AddressConflictError, self).__init__(
            "Packet for UAV #{0.id} received from source address on "
            "communication medium {1!r} but the address does "
            "not belong to the UAV ({2!r})".format(uav, medium, address)
        )
        self.uav = uav
        self.medium = medium
        self.address = address
