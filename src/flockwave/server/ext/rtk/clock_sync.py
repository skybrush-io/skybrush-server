"""Class that validates whether the clock of the GPS is in sync with the
clock of the computer running the server.
"""

from blinker import Signal
from datetime import datetime
from struct import Struct
from typing import ClassVar

from flockwave.gps.ubx.enums import UBXClass, UBXNAVSubclass
from flockwave.gps.ubx.packet import UBXPacket

from .types import GPSPacket


class GPSClockSynchronizationValidator:
    """Class that validates whether the clock of the GPS is in sync with the
    clock of the computer running the server.
    """

    _are_clocks_in_sync: bool = True
    """Stores whether the server clock is assumed to be in sync with the GPS
    clock.
    """

    _ubx_nav_timeutc_struct: ClassVar[Struct] = Struct("<12xHBBBBB")

    sync_state_changed: Signal = Signal(
        doc=(
            "Signal sent whenever the validator detects that the GPS clock is "
            "out of sync with the server, or whenever sync is restored."
        )
    )

    @property
    def are_clocks_in_sync(self) -> bool:
        """Returns whether the server clock is assumed to be in sync with the GPS
        clock.
        """
        return self._are_clocks_in_sync

    @are_clocks_in_sync.setter
    def are_clocks_in_sync(self, value: bool) -> None:
        if bool(value) == self._are_clocks_in_sync:
            return

        self._are_clocks_in_sync = bool(value)
        self.sync_state_changed.send(self, in_sync=self._are_clocks_in_sync)

    def assume_sync(self) -> None:
        """Forces the validator to assume that the server clock and the GPS
        clock are in sync.
        """
        self.are_clocks_in_sync = True

    def notify(self, packet: GPSPacket) -> None:
        """Notifies the clock synchronization validator about the arrival of a
        new packet from the GPS.
        """
        if (
            isinstance(packet, UBXPacket)
            and packet.class_id == UBXClass.NAV
            and packet.subclass_id == UBXNAVSubclass.TIMEUTC
        ):
            self._handle_ubx_nav_timeutc(packet)

    def _handle_ubx_nav_timeutc(self, packet: UBXPacket) -> None:
        """Handles a NAV-TIMEUTC packet by parsing the UTC datetime and checking
        whether it matches the current date/time on the computer running the
        server.
        """
        payload = packet.payload
        if len(payload) < 20:
            # Invalid or short packet
            return

        if payload[19] & 0x04 != 0x04:
            # Packet does not contain a valid UTC timestamp
            return

        try:
            struct = self._ubx_nav_timeutc_struct
            dt = datetime(*struct.unpack(payload[: struct.size]))
            delta = dt - datetime.utcnow()
        except Exception:
            return

        self.are_clocks_in_sync = delta.total_seconds() < 1
