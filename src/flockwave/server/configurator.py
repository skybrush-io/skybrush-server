"""Helper classes and functions for handling configurations from multiple
sources.
"""

import errno
import os

from importlib import import_module
from logging import Logger
from typing import Any, Dict, Optional

__alL__ = ("AppConfigurator", "Configuration")

Configuration = Dict[str, Any]


class AppConfigurator:
    """Helper object that manages loading the configuration of the app from
    various sources.
    """

    def __init__(
        self,
        config: Optional[Configuration] = None,
        *,
        default_filename: Optional[str] = None,
        environment_variable: Optional[str] = None,
        log: Optional[Logger] = None,
        package_name: str = None
    ):
        """Constructor.

        Parameters:
            config: the configuration object that the configurator will
                populate. May contain default values.
            default_filename: name of the default configuration file that the
                configurator will look for in the current working directory
            environment_variable: name of the environment variable in which
                the configurator will look for the name of an additional
                configuration file to load
            package_name: name of the package to import the base configuration
                of the app from
        """
        self._config = config if config is not None else {}
        self._default_filename = default_filename
        self._environment_variable = environment_variable
        self._log = log
        self._package_name = package_name

    def configure(self, filename: Optional[str] = None) -> Configuration:
        """Configures the application.

        Parameters:
            filename: name of the configuration file to load, passed from the
                command line

        Returns:
            bool: whether the configuration sources were processed successfully
        """
        return self._load_configuration(filename)

    @property
    def result(self) -> Configuration:
        """Returns the result of the configuration process."""
        return self._config

    def _load_base_configuration(self) -> None:
        """Loads the default configuration of the application from the
        `flockctrl.server.config` module.
        """
        if not self._package_name:
            config = None
        else:
            try:
                config = import_module(".config", self._package_name)
            except ModuleNotFoundError:
                config = None

        if config:
            self._load_configuration_from_object(config)

    def _load_configuration(self, config: Optional[str] = None) -> bool:
        """Loads the configuration of the application from the following
        sources, in the following order:

        - The default configuration in the `.config` module of the current
          package, if there is one.

        - The configuration file referred to by the `config` argument,
          if present. If it is `None` and a default configuration filename
          was specified at construction time, it will be used instead.

        - The configuration file referred to by the environment variable
          provided at construction time, if it is specified.

        Parameters:
            config: name of the configuration file to load

        Returns:
            bool: whether all configuration files were processed successfully
        """
        self._load_base_configuration()

        config_files = []

        if config:
            config_files.append((config, True))
        elif self._default_filename:
            config_files.append((self._default_filename, False))

        if self._environment_variable:
            config_files.append((os.environ.get(self._environment_variable), True))

        return all(
            self._load_configuration_from_file(config_file, mandatory)
            for config_file, mandatory in config_files
            if config_file
        )

    def _load_configuration_from_file(
        self, filename: str, mandatory: bool = True
    ) -> bool:
        """Loads configuration settings from the given file.

        Parameters:
            filename: name of the configuration file to load. Relative
                paths are resolved from the current directory.
            mandatory: whether the configuration file must exist.
                If this is ``False`` and the file does not exist, this
                function will not print a warning about the missing file
                and pretend that loading the file was successful.

        Returns:
            whether the configuration was loaded successfully
        """
        original, filename = filename, os.path.abspath(filename)

        exists = True
        try:
            config = {}
            with open(filename, mode="rb") as config_file:
                exec(compile(config_file.read(), filename, "exec"), config)
        except IOError as e:
            if e.errno in (errno.ENOENT, errno.EISDIR, errno.ENOTDIR):
                exists = False
            else:
                raise

        self._load_configuration_from_dict(config)

        if not exists and mandatory:
            if self._log:
                self._log.warn("Cannot load configuration from {0!r}".format(original))
            return False
        elif exists:
            if self._log:
                self._log.info("Loaded configuration from {0!r}".format(original))

        return True

    def _load_configuration_from_dict(self, config: Dict[str, Any]) -> None:
        """Loads configuration settings from the given Python dictionary.

        Only uppercase keys will be processed.

        Parameters:
            config: the configuration dict to load.
        """
        for key, value in config.items():
            if key.isupper():
                self._config[key] = value

    def _load_configuration_from_object(self, config: Any) -> None:
        """Loads configuration settings from the given Python object.

        Only uppercase keys will be processed.

        Parameters:
            config: the configuration object to load.
        """
        for key in dir(config):
            if key.isupper():
                self._config[key] = getattr(config, key)
