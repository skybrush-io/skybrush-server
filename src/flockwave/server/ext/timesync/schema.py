"""Configuration schema for the `timesync` extension."""

from pydantic import BaseModel, Field

from .constants import (
    DEFAULT_OFFSET_LOG_THRESHOLD,
    DEFAULT_SOURCE_EXPIRY_THRESHOLD,
    DEFAULT_SYNC_THRESHOLD,
)

__all__ = ("schema",)


class TimeSyncConfig(BaseModel):
    """Configuration model for the timesync extension."""

    sync_threshold: float = Field(
        default=DEFAULT_SYNC_THRESHOLD,
        ge=0,
        title="Sync threshold",
        description=(
            "Maximum absolute clock offset, in seconds, for the local server "
            "clock to be considered synchronized to wall clock time."
        ),
    )

    source_expiry_threshold: float = Field(
        default=DEFAULT_SOURCE_EXPIRY_THRESHOLD,
        gt=0,
        title="Source expiry threshold",
        description=(
            "Maximum age of a timestamp submission, in seconds, after which "
            "it is ignored."
        ),
    )

    offset_log_threshold: float = Field(
        default=DEFAULT_OFFSET_LOG_THRESHOLD,
        ge=0,
        title="Offset log threshold",
        description=(
            "Minimum change in the selected clock offset, in seconds, that "
            "triggers a new log message while the synchronization state stays the same."
        ),
    )


schema = TimeSyncConfig
