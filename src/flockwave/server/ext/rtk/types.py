from flockwave.gps.nmea import NMEAPacket
from flockwave.gps.rtcm.packets import RTCMPacket
from flockwave.gps.ubx import UBXPacket

__all__ = ("GPSPacket",)

GPSPacket = NMEAPacket | RTCMPacket | UBXPacket
"""Union type matching all the GPS packets that we expect on the wire."""
