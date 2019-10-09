"""Connection factory object that allows connections to be constructed
from a simple string or dict representation.
"""

from functools import partial
from urllib.parse import parse_qs, urlparse

from .errors import UnknownConnectionTypeError


__all__ = ("ConnectionFactory", "create_connection", "create_connection_factory")


class ConnectionFactory(object):
    """Connection factory object that creates connections from a URL-like
    string representation or a simple dict representation.
    """

    def __init__(self):
        """Constructor."""
        self._registry = dict()

    def create(self, specification):
        """Creates a connection object from its specification. The
        specification may be written in one of two forms: a single URL-style
        string or a dictionary with prescribed keys and values.

        When the specification is a string, it must follow the following
        URL-like format::

            scheme:[//host:port]/path?param1=value1&param2=value2&...

        where ``scheme`` defines the registered name of the connection class
        (e.g., ``serial`` for serial ports, ``file`` for files and so on),
        ``path`` defines the target of the connection (e.g., the serial
        port itself or the name of the file), ``host`` and ``port`` define
        the hostname and the port where the path is found (if it makes sense
        for the given type of connetion) and the remaining parameters
        and values define additional connection arguments. The ``scheme``
        will be used to look up the connection class (or callable) registered
        in this factory, then it will be called with ``host``, ``port``,
        ``path`` and the additional parameters as keyword arguments. For
        instance, assuming that the ``serial`` scheme resolves to the
        SerialConnection_ class, the following URL::

            serial:/dev/ttyUSB0?baud=115200

        is resolved to the following call::

            SerialConnection(path="/dev/ttyUSB0", baud=115200)

        Parameter value that contain digits and positive/negative signs
        only will be cast to an integer before passing them to the
        connection class. Note that fractional numbers will *not* be cast
        to floats (to avoid any precision loss or rounding errors) - it is
        up to the connection class to handle floats appropriately.

        The other possible specification is in the format of a dictionary
        like the one below::

            {
                "type": "serial",
                "path": "/dev/ttyUSB0",
                "parameters": {
                    "param1": "value1",
                    "param2": "value2"
                }
            }

        When this specification is used, the ``type`` member of the
        dictionary will be used to look up the connection class (or
        callable) in the factory, the ``host``, ``port`` and ``path``
        members will be merged with the ``parameters`` dictionary (if any)
        and the merged dictionary will be passed as keyword arguments.
        ``host``, ``port``, ``path`` and ``parameters`` are all optional.
        No automatic type conversion is performed on the members of the
        ``parameters`` dict.

        Parameters:
            specification (str or dict): the specification of the connection
                to create, in one of the two possible formats outlined above

        Returns:
            Connection: the connection that was created by the factory

        Raises:
            UnknownConnectionTypeError: if the type of the connection is not
                known to the server
        """
        if isinstance(specification, str):
            specification = self._url_specification_to_dict(specification)

        connection_type = specification["type"]
        func = self._registry.get(connection_type)
        if func is None:
            raise UnknownConnectionTypeError(connection_type)

        parameters = {}
        for name in ("host", "port", "path"):
            if name in specification:
                parameters[name] = specification[name]
        parameters.update(specification.get("parameters", {}))
        return func(**parameters)

    def register(self, name, klass=None):
        """Registers the given class for this connection factory with the
        given name, or returns a decorator that will register an arbitrary
        class with the given name (if no class is specified).

        Parameters:
            name (str): the name that will be used in the factory to refer
                to the given connection class. See the `create()`_ method
                for more information about how this name is used.
            klass (Optional[class]): a connection class or a callable that
                returns a new connection instance when called with some
                keyword arguments (that are provided by the factory in
                the `create()`_ method).

        Returns:
            when ``klass`` is not ``None``, returns the class itself. When
            ``klass`` is ``None``, returns a decorator that can be applied
            on a class to register it with the given name in this factory.
        """
        if klass is None:
            return partial(self.register, name)
        else:
            self._registry[name] = klass
            return klass

    @staticmethod
    def _url_specification_to_dict(specification):
        """Converts a URL-styled specification to a dict-styled
        specification.

        Parameters:
            specification (str): the URL-styled specification to convert

        Returns:
            dict: the dict-styled specification
        """
        # Break up the URL into parts
        parts = urlparse(specification, allow_fragments=False)

        # Split the netloc into hostname and port if needed
        host, _, port = parts.netloc.partition(":")
        port = int(port) if port else None

        # Parse the parameters into a dict, turning values into integers
        # where applicable
        parameters = parse_qs(parts.query) if parts.query else {}
        for k, v in parameters.items():
            if len(v) > 1:
                raise ValueError("repeated parameters are not supported")
            v = v[0]
            try:
                v = int(v)
            except ValueError:
                pass
            parameters[k] = v

        # Return the result in dict-styled format
        result = {"type": parts.scheme, "parameters": parameters}
        if host:
            result["host"] = host
        if port is not None:
            result["port"] = port
        if parts.path:
            result["path"] = parts.path
        return result

    def __call__(self, *args, **kwds):
        """Forwards the invocation to the `create()`_ method."""
        return self.create(*args, **kwds)


create_connection = ConnectionFactory()  #: Singleton connection factory


def create_connection_factory(*args, **kwds):
    """Creates a connection factory function that creates a connection
    configured in a specific way when invoked with no arguments.

    This is essentially a deferred call to `create_connection()`
    """
    return partial(create_connection, *args, **kwds)
