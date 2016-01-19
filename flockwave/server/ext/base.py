"""Base class for extensions."""

__all__ = ("ExtensionBase", )


class ExtensionBase(object):
    """Interface specification for Flockwave server extensions."""

    def __init__(self):
        """Constructor."""
        self._app = None
        self.log = None

    @property
    def app(self):
        """The application that the server is attached to."""
        return self._app

    @app.setter
    def app(self, value):
        self._app = value

    def configure(self, configuration):
        """Configures the extension with the given configuration object.

        This method is called only once from `load()`_ during the
        initialization of the extension.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def load(self, app, configuration, logger):
        """Handler that is called by the extension manager when the
        extension is loaded into the server.

        Arguments:
            app (FlockwaveServer): the server application
            configuration (dict): the extension-specific configuration
                dictionary of the server
            logger (logging.Logger): a logger object that the extension
                may use to write to the server log
        """
        self.app = app
        self.log = logger
        self.configure(configuration)

    def spindown(self):
        """Handler that is called by the extension manager when the
        last client disconnects from the server.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def spinup(self):
        """Handler that is called by the extension manager when the
        first client connects to the server.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def unload(self):
        """Handler that is called by the extension manager when the
        extension is unloaded.
        """
        self.log = None
        self.app = None
