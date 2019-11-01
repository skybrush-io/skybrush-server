from tinyrpc.dispatch import public

from flockwave.server.version import __version__


class DockRPCServer:
    """Instance containing the methods that will be executed in response to
    RPC requests coming from the dock instance.

    Note that method names in this class are camelCased to match the method
    names used on the wire.
    """

    @public
    def getVersion(self):
        return __version__

    @public
    def notifyExternalTemperature(self, value: float) -> None:
        """Notifies the server that the external temperature of the docking
        station has changed.

        Parameters:
            value: the new external temperature
        """
        print(f"Dock says new external temperature is: {value}")

    @public
    def notifyInternalTemperature(self, value: float) -> None:
        """Notifies the server that the internal temperature of the docking
        station has changed.

        Parameters:
            value: the new internal temperature
        """
        print(f"Dock says new internal temperature is: {value}")
