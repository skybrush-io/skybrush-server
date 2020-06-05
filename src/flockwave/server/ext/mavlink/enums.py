from enum import IntEnum

__all__ = ("MAVComponent",)


class MAVComponent(IntEnum):
    """Replica of the `MAV_COMPONENT` enum of the MAVLink protocol, using proper
    Python enums.

    Not all values are listed here, only the ones that we do actually use.
    """

    MISSIONPLANNER = 190
