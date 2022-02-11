"""Object that collects basic statistics about the contents of the current
RTK stream so we can show them to the user in the response of an RTK-STAT
message.
"""

from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntFlag
from time import monotonic
from typing import Deque, Dict, Optional, Tuple

from flockwave.gps.rtcm.packets import (
    RTCMPacket,
    RTCMV2Packet,
    RTCMV3Packet,
    RTCMV3StationaryAntennaPacket,
    RTCMV3AntennaDescriptorPacket,
    RTCMV3ExtendedAntennaDescriptorPacket,
)
from flockwave.gps.ubx.enums import UBXClass, UBXNAVSubclass
from flockwave.gps.ubx.packet import UBXPacket
from flockwave.gps.vectors import (
    ECEFCoordinate,
    ECEFToGPSCoordinateTransformation,
    GPSCoordinate,
)

from flockwave.server.utils import LastUpdatedOrderedDict

from .types import GPSPacket

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
    position_ecef: Optional[ECEFCoordinate] = None

    _antenna_position_timestamp: float = 0.0

    @staticmethod
    def is_antenna_related_packet(packet: GPSPacket) -> bool:
        """Returns whether the given GPS packet conveys information that
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
        self.position_ecef = None
        self._antenna_position_timestamp = monotonic()

    @property
    def json(self):
        """Returns the JSON representation of this object that we post in the
        response of an RTK-STAT message.
        """
        if self.position:
            self._forget_old_antenna_position_if_needed()
        return {
            "stationId": self.station_id,
            "descriptor": self.descriptor,
            "serialNumber": self.serial_number,
            "position": self.position,
            "positionECEF": self.position_ecef,
        }

    def notify(self, packet: RTCMV3Packet) -> None:
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
            self.position_ecef = position * 1000  # [m] -> [mm]
            self.position_ecef.round(0)
            self._antenna_position_timestamp = monotonic()

    def _forget_old_antenna_position_if_needed(self) -> None:
        """Clears the position of the antenna we have not received another
        antenna position packet for the last 30 seconds.
        """
        now = monotonic()
        if now - self._antenna_position_timestamp >= 30:
            self.position = None
            self.position_ecef = None


@dataclass
class MessageObservations:
    entries: Deque[Tuple[float, float]] = field(default_factory=deque)

    _last_observed_at: float = field(default_factory=monotonic)
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
        return monotonic() - self._last_observed_at

    @property
    def json(self):
        """Returns the JSON summary of the observations that is used in an
        RTK-STAT message.
        """
        self._flush_old_observations()
        return [
            int(self.age_of_last_observation * 1000),
            self._total_bytes * 8 / 10,  # bits per second, we keep 10 seconds
        ]

    def _flush_old_observations(self) -> None:
        """Removes old observations from the queue; these will not be used to
        determine the current bit rate.

        Right now we use observations from the last 10 seconds only to estimate
        the bit rate.
        """
        now = monotonic()
        while self.entries:
            head = self.entries[0]
            if now - head[1] > 10:
                self._total_bytes -= head[0]
                self.entries.popleft()
            else:
                break


@dataclass
class SatelliteCNRs:
    entries: Dict[str, float] = field(default_factory=dict)
    _timestamps: LastUpdatedOrderedDict[str, float] = field(
        default_factory=LastUpdatedOrderedDict
    )

    @staticmethod
    def has_satellite_info(packet: GPSPacket) -> bool:
        """Returns whether the given GPS packet conveys information about
        satellite carrier-to-noise ratios.
        """
        return hasattr(packet, "satellites")

    def add(self, packet: RTCMPacket, timestamp: Optional[float] = None) -> None:
        """Update the locally stored information about the satellites based on
        the given satellite list retrieved from an RTCM packet.
        """
        if timestamp is None:
            timestamp = monotonic()

        for satellite in packet.satellites:  # type: ignore
            id = getattr(satellite, "id", None)
            if not id:
                continue

            cnr = getattr(satellite, "cnr", None)
            if cnr is None:
                continue

            if hasattr(cnr, "__iter__"):
                # multiple CNRs (e.g., for L1 and L2 channels), take the maximum,
                # which is typically the L1 channel that we are interested in
                # anyway -- plus it's consistent with how the MSM packet CNR
                # is calculated
                cnr = max(cnr)

            self.entries[id] = cnr
            self._timestamps[id] = timestamp

    def clear(self) -> None:
        """Clears all satellite CNR observations."""
        self.entries.clear()
        self._timestamps.clear()

    @property
    def json(self):
        """Returns the JSON summary of the satellite carrier-to-noise ratios
        that is used in an RTK-STAT message.

        Do not modify the dictionary returned from this function; it is the
        same as the internal dictionary that is used to store the entries.
        """
        self._flush_old_observations()
        return self.entries

    def _flush_old_observations(self) -> None:
        """Removes old observations about satellites for which we have no recent
        information in the last 15 seconds.
        """
        threshold = monotonic() - 15

        if self._timestamps and self._timestamps.first_value <= threshold:
            while self._timestamps:
                key, timestamp = self._timestamps.popitem(last=False)
                if not timestamp <= threshold:
                    self._timestamps[key] = timestamp
                    break
                else:
                    del self.entries[key]


class SurveyStatusFlag(IntFlag):
    """Status flags for a survey status object."""

    #: Indicates that the survey status is unknown
    UNKNOWN = 0

    #: Indicates that the survey status is supported on the GPS receiver
    SUPPORTED = 1

    #: Indicates that the GPS receiver is Surveyg its own position
    ACTIVE = 2

    #: Indicates that the GPS receiver has a valid estimate of its own position
    VALID = 4


@dataclass
class SurveyStatus:
    """Object that stores the status of the current survey procedure."""

    #: Stores the estimated accuracy of the surveyed position, in meters; valid
    #: only if the "known" flag is set
    accuracy: float = 0.0

    #: Status flags
    flags: SurveyStatusFlag = SurveyStatusFlag.UNKNOWN

    @staticmethod
    def is_survey_related_packet(packet: GPSPacket) -> bool:
        """Returns whether the given GPS packet conveys information that
        relates to the survey procedure.
        """
        return (
            isinstance(packet, UBXPacket)
            and packet.class_id == UBXClass.NAV
            and packet.subclass_id == UBXNAVSubclass.SVIN
        )

    @property
    def active(self) -> bool:
        """Returns whether the survey is in progress."""
        return bool(self.flags & SurveyStatusFlag.ACTIVE)

    @property
    def json(self):
        """Returns the JSON representation of the survey status object."""
        return {"accuracy": self.accuracy, "flags": self.flags}

    @property
    def supported(self) -> bool:
        """Returns whether the survey procedure is supported."""
        return bool(self.flags & SurveyStatusFlag.SUPPORTED)

    @property
    def valid(self) -> bool:
        """Returns whether the surveyed coordinate is valid."""
        return bool(self.flags & SurveyStatusFlag.VALID)

    def clear(self) -> None:
        """Clears the contents of the survey info object."""
        self.flags = SurveyStatusFlag.UNKNOWN
        self.accuracy = 0.0

    def notify(self, packet: UBXPacket) -> None:
        """Notifies the survey object about the arrival of a new packet."""
        # We have a UBX NAV-SVIN packet so get the survey status from there
        self.accuracy = int.from_bytes(packet.payload[28:32], "little") / 10000.0
        self.flags = SurveyStatusFlag.SUPPORTED
        if packet.payload[36]:
            self.flags |= SurveyStatusFlag.VALID
        if packet.payload[37]:
            self.flags |= SurveyStatusFlag.ACTIVE

    def set_to_fixed_with_accuracy(self, accuracy: float) -> None:
        """Notifies the survey object that the base station was switched to
        fixed-coordinate mode with the given accuracy.
        """
        self.flags = SurveyStatusFlag.SUPPORTED | SurveyStatusFlag.VALID
        self.accuracy = accuracy


class RTKStatistics:
    """Object that collects basic statistics about the contents of the current
    RTK stream so we can show them to the user in the response of an RTK-STAT
    message.
    """

    def __init__(self):
        """Constructor."""
        self._message_observations = defaultdict(MessageObservations)
        self._satellite_cnrs = SatelliteCNRs()
        self._antenna_information = AntennaInformation()
        self._survey_status = SurveyStatus()
        self.clear()

    def clear(self) -> None:
        """Clears the contents of the RTK statistics object."""
        self._antenna_information.clear()
        self._message_observations.clear()
        self._satellite_cnrs.clear()
        self._survey_status.clear()

    @property
    def json(self):
        """Returns the JSON representation of this object that we post in the
        response of an RTK-STAT message.
        """
        return {
            "antenna": self._antenna_information,
            "messages": self._message_observations,
            "cnr": self._satellite_cnrs,
            "survey": self._survey_status,
        }

    def notify(self, packet: GPSPacket) -> None:
        """Notifies the statistics object about the arrival of a new packet."""
        if isinstance(packet, (RTCMV2Packet, RTCMV3Packet)):
            type = self._get_rtcm_packet_type(packet)
            self._message_observations[type].add(packet, monotonic())

        if SatelliteCNRs.has_satellite_info(packet):
            self._satellite_cnrs.add(packet, monotonic())  # type: ignore

        if AntennaInformation.is_antenna_related_packet(packet):
            self._antenna_information.notify(packet)  # type: ignore

        if SurveyStatus.is_survey_related_packet(packet):
            self._survey_status.notify(packet)  # type: ignore

    def set_to_fixed_with_accuracy(self, accuracy: float) -> None:
        """Sets the base station statistics object to fixed mode with the given
        known survey accuracy.
        """
        self._survey_status.set_to_fixed_with_accuracy(accuracy)

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

    def _get_rtcm_packet_type(self, packet: RTCMPacket) -> str:
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
