from typing import Union

from flockwave.gps.nmea import NMEAPacket
from flockwave.gps.rtcm.packets import RTCMPacket
from flockwave.gps.ubx import UBXPacket

__all__ = ("GPSPacket",)

#: Union type matching all the GPS packets that we expect on the wire
GPSPacket = Union[NMEAPacket, RTCMPacket, UBXPacket]
