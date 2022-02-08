"""Extension that routes log messages to a logging folder on the disk."""

from __future__ import annotations

from enum import Enum
from logging import FileHandler, Handler, Logger, getLogger
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from flockwave.logger.formatters import styles

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

handler: Optional[Handler] = None
log: Optional[Logger] = None
log_dir: Optional[Path] = None

LOG_FILENAME: str = "skybrushd.log"


class LogRotationPolicy(Enum):
    OFF = "off"
    HOURLY = "hourly"
    DAILY = "daily"


def load(app: "SkybrushServer", configuration: Dict[str, Any], log: Logger):
    global handler

    log_dir = Path(str(configuration.get("folder", "")) or app.dirs.user_log_dir)
    log.info(f"Storing logs in '{log_dir}'")

    format_str = str(configuration.get("format", "tabular"))
    try:
        formatter = styles[format_str]
    except KeyError:
        log.warn(f"Unknown log format: {format_str!r}, assuming tabular")
        formatter = styles["tabular"]

    rotate_str = str(configuration.get("rotate", LogRotationPolicy.DAILY.value))
    try:
        rotate = LogRotationPolicy(rotate_str)
    except ValueError:
        log.warn(f"Unknown log rotation policy: {rotate_str!r}, assuming daily")
        rotate = LogRotationPolicy.DAILY

    keep_str = str(configuration.get("keep", 0))
    try:
        backup_count = int(keep_str)
    except ValueError:
        backup_count = -1

    if backup_count < 0:
        log.warn(
            f"Invalid backup count: {keep_str!r}, assuming that all logs should be kept"
        )
        backup_count = 0

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_dir_exists = True
    except Exception:
        log.error(f"Failed to create log folder at {log_dir}, logging disabled")
        log_dir_exists = False

    if log_dir_exists:
        log_filename = log_dir / LOG_FILENAME
        if rotate is LogRotationPolicy.OFF:
            handler = FileHandler(log_filename, delay=True)
        elif rotate is LogRotationPolicy.HOURLY:
            handler = TimedRotatingFileHandler(
                log_filename, when="h", backupCount=backup_count, delay=True
            )
        elif rotate is LogRotationPolicy.DAILY:
            handler = TimedRotatingFileHandler(
                log_filename, when="d", backupCount=backup_count, delay=True
            )
        else:
            handler = None

    if handler:
        handler.setFormatter(formatter())
        getLogger().addHandler(handler)
        log.info("Logging started")


def unload(app: "SkybrushServer"):
    global handler, log

    if handler is not None:
        getLogger().removeHandler(handler)
        handler = None


description = "Routing of log messages to a logging folder on the disk"
schema = {
    "properties": {
        "folder": {
            "type": "string",
            "title": "Full, absolute path to the logging folder",
            "description": "Log files will be stored in this folder. Leave empty to use the default log folder.",
            "default": "",
        },
        "format": {
            "type": "string",
            "enum": ["tabular", "json"],
            "title": "Format of the log file",
            "default": "tabular",
            "options": {"enum_titles": ["Tabular", "JSON"]},
        },
        "rotate": {
            "type": "string",
            "enum": [e.value for e in LogRotationPolicy],
            "title": "Log rotation",
            "description": "Setting this option will close the current log file and open a new one at regular intervals.",
            "default": "daily",
            "options": {
                "enum_titles": [
                    "Do not rotate log files",
                    "Rotate log file once every hour",
                    "Rotate log file once per day",
                ]
            },
        },
        "keep": {
            "type": "integer",
            "minValue": 0,
            "title": "Number of backups to keep",
            "description": "Set to zero to keep all log files",
        },
    }
}
