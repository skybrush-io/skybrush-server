"""Extension that routes log messages to a logging folder on the disk."""

from __future__ import annotations

from logging import Handler, Logger, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from flockwave.logger.formatters import styles
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

handler: Handler | None = None
log: Logger | None = None
log_dir: Path | None = None

LOG_FILENAME: str = "skybrushd.log"


class LoggingConfig(BaseModel):
    """Configuration model for the logging extension."""

    folder: str = Field(
        default="",
        title="Full, absolute path to the logging folder",
        description=(
            "Log files will be stored in this folder. Leave empty to use "
            "the default log folder."
        ),
    )

    format: Literal["tabular", "json"] = Field(
        default="tabular",
        title="Format of the log file",
        json_schema_extra={
            "options": {"enum_titles": ["Tabular", "JSON"]},
        },
    )

    size: int = Field(
        default=1000000,
        ge=0,
        title="Maximum log file size",
        description=(
            "Maximum allowed size of a log file, in bytes. Log files will be "
            "rotated when they reach this size. Use zero for unlimited logs "
            "(i.e. no rotation)."
        ),
    )

    keep: int = Field(
        default=0,
        ge=0,
        title="Number of backups to keep",
        description="Set to zero to keep all log files",
    )


def load(app: "SkybrushServer", configuration: LoggingConfig, log: Logger):
    global handler

    log_dir = Path(configuration.folder or app.dirs.user_log_dir)
    log.info(f"Storing logs in '{log_dir}'")

    format_str = configuration.format
    try:
        formatter = styles[format_str]
    except KeyError:
        log.warning(f"Unknown log format: {format_str!r}, assuming tabular")
        formatter = styles["tabular"]

    size_limit = configuration.size
    backup_count = configuration.keep
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_dir_exists = True
    except Exception:
        log.error(f"Failed to create log folder at {log_dir}, logging disabled")
        log_dir_exists = False

    if log_dir_exists:
        log_filename = log_dir / LOG_FILENAME
        handler = RotatingFileHandler(
            log_filename,
            maxBytes=size_limit,
            backupCount=backup_count,
            delay=True,
            encoding="utf-8",
        )

    if handler:
        handler.setFormatter(formatter())
        getLogger().addHandler(handler)
        log.info("Logging started")


def unload(app: "SkybrushServer"):
    global handler, log

    if handler is not None:
        handler.close()
        getLogger().removeHandler(handler)
        handler = None


description = "Routing of log messages to a logging folder on the disk"
schema = LoggingConfig
