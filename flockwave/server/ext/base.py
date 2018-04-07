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
        old_value = self._app
        self._app = value
        self.on_app_changed(old_value, self._app)

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

        Typically, you don't need to override this method; override
        `configure()` instead.

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

    def on_app_changed(self, old_app, new_app):
        """Handler that is called when the extension is associated to an
        application.

        Arguments:
            old_app (FlockwaveServer): the old server application
            new_app (FlockwaveServer): the new server application
        """
        pass

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

    def teardown(self):
        """Tears down the extension and prepares it for unloading.

        This method is called only once from `unload()`_ during the
        unloading of the extension.

        The default implementation of this method is empty. There is no
        need to call the superclass when you override it.
        """
        pass

    def unload(self, app):
        """Handler that is called by the extension manager when the
        extension is unloaded.

        Typically, you don't need to override this method; override
        `teardown()` instead.

        Arguments:
            app (FlockwaveServer): the server application; provided for sake
                of API compatibility with simple classless extensions where
                the module provides a single `unload()` function
        """
        self.teardown()
        self.log = None
        self.app = None


class UAVExtensionBase(ExtensionBase):
    """Base class for extensions that intend to provide support for a
    specific type of UAVs.

    Subclasses should override the ``_create_driver()`` method to create
    the actual driver instance that the extension will use, and the
    ``configure_driver()`` method to configure the driver.
    """

    def __init__(self):
        super(UAVExtensionBase, self).__init__()
        self._driver = None

    def create_device_tree_mutation_context(self):
        """Returns a context that can be used in a ``with`` statement to
        encapsulate a block of code that may modify the channels in the
        device tree of some UAVs.

        Modifications to the channel nodes in a device tree should always be
        done in a mutation context to ensure that clients are notified about
        the modifications.
        """
        return self.app.device_tree.create_mutator()

    def configure(self, configuration):
        super(UAVExtensionBase, self).configure(configuration)
        self.configure_driver(self.driver, configuration)

    def configure_driver(self, driver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver (UAVDriver): the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        pass

    def _create_driver(self):
        """Creates the driver object that the extension will use. It is
        not required to associate the driver to the current application;
        the extension will do it.

        Returns:
            Optional[UAVDriver]: the driver that the extension will use,
                or ``None`` if the extension does not need a driver
        """
        return None

    def _update_driver_from_app(self):
        """Updates the driver object in the extension when the associated
        app has changed.
        """
        app = self.app
        if app is None:
            self._driver = None
        else:
            self._driver = self._create_driver()
            if self._driver is not None:
                self._driver.app = app

    @property
    def driver(self):
        """The driver that is responsible for handling communication with
        the UAVs that the extension provides.
        """
        return self._driver

    def on_app_changed(self, old_app, new_app):
        self._update_driver_from_app()
