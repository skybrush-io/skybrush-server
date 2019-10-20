from tinyrpc.dispatch import public


class DockRPCServer:
    """Instance containing the methods that will be executed in response to
    RPC requests coming from the dock instance.

    Note that method names in this class are camelCased to match the method
    names used on the wire.
    """

    @public
    def getVersion(self):
        return "0.1.0"
