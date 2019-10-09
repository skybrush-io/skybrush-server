"""Logger object for the Flockwave server."""

import logging

from colorlog import default_log_colors
from colorlog.colorlog import ColoredRecord
from colorlog.escape_codes import escape_codes, parse_colors
from functools import lru_cache, partial
from typing import Any, Dict

__all__ = ("add_id_to_log", "log", "install", "LoggerWithExtraData")


log = logging.getLogger(__name__.rpartition(".")[0])


default_log_symbols = {
    "DEBUG": u" ",
    "INFO": u" ",
    "WARNING": u"\u25b2",  # BLACK UP-POINTING TRIANGLE
    "ERROR": u"\u25cf",  # BLACK CIRCLE
    "CRITICAL": u"\u25cf",  # BLACK CIRCLE
}


@lru_cache(maxsize=256)
def _get_short_name_for_logger(name):
    return name.rpartition(".")[2]


class ColoredFormatter(logging.Formatter):
    """Logging formatter that adds colors to the log output.

    Colors are added based on the log level and other semantic information
    stored in the log record.
    """

    def __init__(self, fmt=None, datefmt=None, log_colors=None, log_symbols=None):
        """
        Constructor.

        Parameters:
            fmt (unicode or None): The format string to use. Note that this
                must be a Unicode string.
            datefmt (unicode or None): The format string to use for dates.
                Note that this must be a Unicode string.
            log_colors (dict): Mapping from log level names to color names
            log_symbols (dict): Mapping from log level names to symbols
        """
        if fmt is None:
            fmt = "{log_color}{levelname}:{name}:{message}{reset}"

        super().__init__(fmt, datefmt, style="{")

        if log_colors is None:
            log_colors = default_log_colors

        self.log_colors = {k: parse_colors(v) for k, v in log_colors.items()}
        self.log_symbols = (
            log_symbols if log_symbols is not None else default_log_symbols
        )

    def format(self, record):
        """Format a message from a log record object."""
        if not hasattr(record, "semantics"):
            record.semantics = None
        if not hasattr(record, "id"):
            record.id = ""

        record = ColoredRecord(record)
        record.log_color = self.get_preferred_color(record)
        record.log_symbol = self.get_preferred_symbol(record)
        record.short_name = _get_short_name_for_logger(record.name)
        message = super().format(record)

        if not message.endswith(escape_codes["reset"]):
            message += escape_codes["reset"]

        return message

    def get_preferred_color(self, record):
        """Return the preferred color for the given log record."""
        color = self.log_colors.get(record.levelname, "")
        if record.levelname == "INFO":
            # For the INFO level, we may override the color with the
            # semantics of the message.
            semantic_color = self.log_colors.get(record.semantics)
            if semantic_color is not None:
                color = semantic_color
        return color

    def get_preferred_symbol(self, record):
        """Return the preferred color for the given log record."""
        symbol = self.log_symbols.get(record.semantics)
        if symbol is not None:
            return symbol
        else:
            return self.log_symbols.get(record.levelname, "")


class LoggerWithExtraData:
    """Object that provides the same interface as Python's standard logging
    functions, but automatically adds default values to the `extra` dict
    of each logging record.
    """

    def __init__(self, log: logging.Logger, extra: Dict[str, Any]):
        """Constructor.

        Parameters:
            log: the logging module to wrap
            extra: extra data to add as default to each logging record
        """
        self._extra = dict(extra)
        self._log = log
        self._methods = {}

    def __getattr__(self, name):
        if name in self._methods:
            return self._methods
        else:
            wrapped_method = getattr(self._log, name)
            method = self._methods[name] = partial(self._call, wrapped_method)
            return method

    def _call(self, func, *args, **kwds):
        extra = kwds.get("extra") or self._extra

        if extra is not self._extra:
            for k, v in self._extra.items():
                if k not in extra:
                    extra[k] = v
        else:
            kwds["extra"] = self._extra

        return func(*args, **kwds)


def add_id_to_log(log: logging.Logger, id: str):
    """Adds the given ID as a permanent extra attribute to the given logger.

    Parameters:
        log: the logger to wrap
        id: the ID attribute to add to the logger

    Returns:
        a new logger that extends the extra dict of each logging record with
        the given ID
    """
    return LoggerWithExtraData(log, {"id": id})


def install(level=logging.INFO):
    """Install a default formatter and stream handler to the root logger of Python.

    This method can be used during startup to ensure that we can see the
    log messages on the console nicely.
    """
    log_colors = dict(default_log_colors)
    log_colors.update(
        DEBUG="bold_black",
        INFO="reset",
        request="bold_blue",
        response_success="bold_green",
        response_error="bold_red",
        notification="bold_yellow",
        success="bold_green",
    )
    log_symbols = dict(default_log_symbols)
    log_symbols.update(
        request=u"\u2190",  # LEFTWARDS ARROW
        response_success=u"\u2192",  # RIGHTWARDS ARROW
        response_error=u"\u2192",  # RIGHTWARDS ARROW
        notification=u"\u2192",  # RIGHTWARDS ARROW
        success=u"\u2714",  # CHECK MARK
        failure=u"\u2718",  # BALLOT X
    )
    formatter = ColoredFormatter(
        "{log_color}{log_symbol}{reset} "
        "{fg_cyan}{short_name:<11.11}{reset} "
        "{fg_bold_black}{id:<10.10}{reset} "
        "{log_color}{message}{reset}",
        log_colors=log_colors,
        log_symbols=log_symbols,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()

    root_logger.addHandler(handler)
    root_logger.setLevel(level)
