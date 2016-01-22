"""Logger object for the Flockwave server."""

import logging

from colorlog import default_log_colors
from colorlog.colorlog import ColoredRecord
from colorlog.escape_codes import escape_codes, parse_colors

__all__ = ("log", "install")


log = logging.getLogger(__name__.rpartition(".")[0])


default_log_symbols = {
    "DEBUG": u" ",
    "INFO": u" ",
    "WARNING": u"\u25b2",           # BLACK UP-POINTING TRIANGLE
    "ERROR": u"\u25cf",             # BLACK CIRCLE
    "CRITICAL": u"\u25cf",          # BLACK CIRCLE
}


class ColoredFormatter(logging.Formatter):
    """Logging formatter that adds colors to the log output.

    Colors are added based on the log level and other semantic information
    stored in the log record.
    """

    def __init__(self, fmt=None, datefmt=None, log_colors=None,
                 log_symbols=None):
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
            fmt = u"%(log_color)s%(levelname)s:%(name)s:%(message)s"

        super(ColoredFormatter, self).__init__(fmt, datefmt)

        if log_colors is None:
            log_colors = default_log_colors

        self.log_colors = {
            k: parse_colors(v)
            for k, v in log_colors.iteritems()
        }
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
        message = super(ColoredFormatter, self).format(record)

        if not message.endswith(escape_codes['reset']):
            message += escape_codes['reset']

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
        success="bold_green"
    )
    log_symbols = dict(default_log_symbols)
    log_symbols.update(
        request=u"\u2190",           # LEFTWARDS ARROW
        response_success=u"\u2192",  # RIGHTWARDS ARROW
        response_error=u"\u2192",    # RIGHTWARDS ARROW
        notification=u"\u2192",      # RIGHTWARDS ARROW
        success=u"\u2714",           # CHECK MARK
        failure=u"\u2718"            # BALLOT X
    )
    formatter = ColoredFormatter(
        "%(fg_bold_black)s%(id)-7.7s "
        "%(log_color)s%(log_symbol)s "
        "%(message)s",
        log_colors=log_colors, log_symbols=log_symbols
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()

    root_logger.addHandler(handler)
    root_logger.setLevel(level)
