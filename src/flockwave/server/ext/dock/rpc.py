from tinyrpc.dispatch import public
from typing import Optional

from flockwave.gps.vectors import GPSCoordinate
from flockwave.server.version import __version__

from .model import Dock


class DockRPCServer:
    """Instance containing the methods that will be executed in response to
    RPC requests coming from the dock instance.

    Note that method names in this class are camelCased to match the method
    names used on the wire.
    """

    def __init__(self):
        """Constructor."""
        self.dock = None  # type: Optional[Dock]
        self.create_mutator = None

    @public
    def getVersion(self):
        return __version__

    @public
    def notifyExternalTemperature(self, value: int) -> None:
        """Notifies the server that the external temperature of the docking
        station has changed.

        Parameters:
            value: the new external temperature, in tenth of degrees
        """
        if not self.dock:
            return

        with self.create_mutator() as mutator:
            self.dock.update_temperatures(mutator, external=value / 10)

    @public
    def notifyInternalTemperature(self, value: int) -> None:
        """Notifies the server that the internal temperature of the docking
        station has changed.

        Parameters:
            value: the new internal temperature, in tenth of degrees
        """
        if not self.dock:
            return

        with self.create_mutator() as mutator:
            self.dock.update_temperatures(mutator, internal=value / 10)

    def process_full_state_update(self, state):
        """Processes a full state update from the docking station in response
        to a `getState()` RPC request.
        """
        with self.create_mutator() as mutator:
            updates = {}
            if state.get("extTemp") is not None:
                updates["external"] = state["extTemp"] / 10
            if state.get("temp") is not None:
                updates["internal"] = state["temp"] / 10
            if updates:
                self.dock.update_temperatures(mutator, **updates)

            if state.get("location") is not None:
                self.dock.update_status(
                    position=GPSCoordinate.from_json(state["location"])
                )
