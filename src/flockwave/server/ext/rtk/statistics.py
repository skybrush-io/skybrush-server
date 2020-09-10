"""Object that collects basic statistics about the contents of the current
RTK stream so we can show them to the user in the response of an RTK-STAT
message.
"""

from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from trio import current_time
from typing import Deque, Iterable, Optional, Tuple

from flockwave.gps.rtcm.packets import (
    RTCMPacket,
    RTCMV2Packet,
    RTCMV3Packet,
    RTCMV3StationaryAntennaPacket,
    RTCMV3AntennaDescriptorPacket,
    RTCMV3ExtendedAntennaDescriptorPacket,
)
from flockwave.gps.vectors import ECEFToGPSCoordinateTransformation, GPSCoordinate

__all__ = ("RTKStatistics",)

#: ECEF-to-GPS transformation used to convert antenna coordinates
_ecef_to_gps = ECEFToGPSCoordinateTransformation()


@dataclass
class AntennaInformation:
    """Simple data class holding information about the current RTK antenna."""

    station_id: Optional[int] = None
    descriptor: Optional[str] = None
    serial_number: Optional[str] = None
    position: Optional[GPSCoordinate] = None

    @staticmethod
    def is_antenna_related_packet(packet: RTCMPacket) -> bool:
        """Returns whether the given RTCM packet conveys information that
        relates to the antenna itself.
        """
        return isinstance(
            packet,
            (
                RTCMV3StationaryAntennaPacket,
                RTCMV3AntennaDescriptorPacket,
                RTCMV3ExtendedAntennaDescriptorPacket,
            ),
        )

    def clear(self) -> None:
        self.station_id = None
        self.descriptor = None
        self.serial_number = None
        self.position = None

    @property
    def json(self):
        """Returns the JSON representation of this object that we post in the
        response of an RTK-STAT message.
        """
        return {
            "stationId": self.station_id,
            "descriptor": self.descriptor,
            "serialNumber": self.serial_number,
            "position": self.position,
        }

    def notify(self, packet: RTCMPacket) -> None:
        """Notifies the statistics object about the arrival of a new packet."""
        station_id = getattr(packet, "station_id", None)
        if station_id is not None:
            self.station_id = station_id

        serial = getattr(packet, "serial", None)
        if serial is not None:
            self.serial_number = serial

        descriptor = getattr(packet, "descriptor", None)
        if descriptor is not None:
            self.descriptor = descriptor

        position = getattr(packet, "position", None)
        if position is not None:
            self.position = _ecef_to_gps.to_gps(position)


@dataclass
class MessageObservations:
    entries: Deque[Tuple[float, float]] = field(default_factory=deque)

    _last_observed_at: float = field(default_factory=current_time)
    _total_bytes: float = 0

    def add(self, packet: RTCMPacket, timestamp: float) -> None:
        if packet.bytes is None:
            # we don't know the original byte-level representation of the
            # packet so we ignore it
            return

        length = len(packet.bytes)
        self._last_observed_at = timestamp
        self._total_bytes += length
        self.entries.append((length, timestamp))

    @property
    def age_of_last_observation(self) -> float:
        """Returns the age of the last observation of this message."""
        return current_time() - self._last_observed_at

    @property
    def json(self):
        """Returns the JSON summary of the observations that is used in an
        RTK-STAT message.
        """
        self._flush_old_observations()
        return [
            self.age_of_last_observation * 1000,
            self._total_bytes * 8 / 10,  # bits per second, we keep 10 seconds
        ]

    def _flush_old_observations(self) -> None:
        """Removes old observations from the queue; these will not be used to
        determine the current bit rate.

        Right now we use observations from the last 10 seconds only to estimate
        the bit rate.
        """
        now = current_time()
        while self.entries:
            head = self.entries[0]
            if now - head[1] > 10:
                self._total_bytes -= head[0]
                self.entries.popleft()
            else:
                break


class RTKStatistics:
    """Object that collects basic statistics about the contents of the current
    RTK stream so we can show them to the user in the response of an RTK-STAT
    message.
    """

    def __init__(self):
        """Constructor."""
        self._message_observations = defaultdict(MessageObservations)
        self._satellite_cnrs = {}
        self._antenna_information = AntennaInformation()
        self.clear()

    def clear(self) -> None:
        """Clears the contents of the RTK statistics object."""
        self._antenna_information.clear()
        self._message_observations.clear()
        self._satellite_cnrs.clear()

    @property
    def json(self):
        """Returns the JSON representation of this object that we post in the
        response of an RTK-STAT message.
        """
        return {
            "antenna": self._antenna_information,
            "messages": self._message_observations,
            "cnr": self._satellite_cnrs,
        }

    def notify(self, packet: RTCMPacket) -> None:
        """Notifies the statistics object about the arrival of a new packet."""
        type = self._get_packet_type(packet)
        self._message_observations[type].add(packet, current_time())

        if hasattr(packet, "satellites"):
            self._update_satellite_status(packet.satellites)

        if AntennaInformation.is_antenna_related_packet(packet):
            self._antenna_information.notify(packet)

    @contextmanager
    def use(self):
        """Context manager that clears the statistics object upon entering
        and exiting the context.
        """
        self.clear()
        try:
            yield
        finally:
            self.clear()

    def _get_packet_type(self, packet: RTCMPacket) -> str:
        """Returns a short description of the type of the packet. The
        description starts with ``rtcm2`` or ``rtcm3``, followed by a slash
        and the numeric packet type.
        """
        if isinstance(packet, RTCMV2Packet):
            return f"rtcm2/{packet.packet_type}"
        elif isinstance(packet, RTCMV3Packet):
            return f"rtcm3/{packet.packet_type}"
        else:
            packet_type = getattr(packet, "packet_type", "???")
            return f"unknown/{packet_type}"

    def _update_satellite_status(self, satellites: Iterable[object]) -> None:
        """Update the locally stored information about the satellites based on
        the given satellite list retrieved from an RTCM packet.
        """
        for satellite in satellites:
            id = getattr(satellite, "id", None)
            if not id:
                continue

            cnr = getattr(satellite, "cnr", None)
            if cnr is None:
                continue

            if hasattr(cnr, "__iter__"):
                # multiple CNRs (e.g., for L1 and L2 channels), take the average
                cnr = sum(cnr) / len(cnr)

            self._satellite_cnrs[id] = cnr
