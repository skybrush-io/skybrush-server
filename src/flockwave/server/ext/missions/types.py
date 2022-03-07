"""Types specific to the mission planning and management extension."""

from enum import Enum

__all__ = ("MissionState",)


class MissionState(Enum):
    """Enum representing the possible states of a single mission on the server."""

    NEW = "new"
    """The mission is newly created and may or may not have been parameterized.
    It is not ready to start yet.
    """

    AUTHORIZED_TO_START = "authorizedToStart"
    """The mission parameters and the mission plan have been finalized and the
    mission is waiting for a scheduled start time or a start signal. Modifications
    to the parameters or the plan are not allowed; the authorization must be
    revoked first to modify parameters further. The start time of the mission
    may still be modified or cleared.
    """

    ONGOING = "ongoing"
    """The mission is ongoing and its scheduled task is running on the server,
    managing the drones that are associated to the mission.
    """

    CANCELLED = "cancelled"
    """The mission was cancelled before it had a chance to start."""

    ABORTED = "aborted"
    """The mission was aborted by an unexpected event or by user intervention
    while it was being executed. The task associated to the mission is not
    running any more.
    """

    SUCCESSFUL = "successful"
    """The mission terminated successfully. The task associated to the mission
    is not running any more.
    """

    FAILED = "failed"
    """The mission terminated but it did not achieve its goals. The task
    associated to the mission is not running any more.
    """
