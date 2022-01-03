from contextlib import ExitStack, contextmanager
from functools import partial
from itertools import count
from typing import Callable, ClassVar, Iterable, Iterator, TYPE_CHECKING, Optional

from flockwave.concurrency import Watchdog
from flockwave.gps.rtcm.packets import RTCMPacket, RTCMV3StationaryAntennaPacket
from flockwave.gps.rtcm.parsers import create_rtcm_parser
from flockwave.gps.vectors import ECEFToGPSCoordinateTransformation

from flockwave.server.utils.generic import overridden

if TYPE_CHECKING:
    from flockwave.server.ext.beacon.model import Beacon
    from flockwave.server.ext.rtk.extension import RTKExtension

__all__ = ("RTKBeaconManager",)


class RTKBeaconManager:
    """Class that manages the status of the beacon that represents the position
    of the RTK base station in the server.
    """

    BEACON_ID_TEMPLATE: ClassVar[str] = "rtk::base_{}"

    enabled: bool = False
    timeout: float = 15

    _counter: Iterator[int]
    _parser: Callable[[bytes], Iterable[RTCMPacket]]
    _trans: ECEFToGPSCoordinateTransformation = ECEFToGPSCoordinateTransformation()
    _watchdog: Optional[Watchdog] = None

    def __init__(self) -> None:
        self._counter = count()

    @contextmanager
    def use(self, ext: "RTKExtension", nursery) -> Iterator[None]:
        if not self.enabled:
            return

        app = ext.app
        if app is None:
            return

        beacon_api = app.import_api("beacon")
        signal_api = app.import_api("signals")
        signal = ext.RTK_PACKET_SIGNAL

        self._parser = create_rtcm_parser()

        with ExitStack() as stack:
            # When switching RTK presets, it may happen that the new beacon is
            # created _before_ the previous beacon is removed so we need to use
            # a counter to ensure that the IDs are unique
            beacon = stack.enter_context(
                beacon_api.use(self.BEACON_ID_TEMPLATE.format(next(self._counter)))
            )
            beacon.basic_properties.name = "RTK base"

            stack.enter_context(
                signal_api.use(
                    {signal: partial(self._on_rtk_packet_received, beacon=beacon)}
                )
            )

            watchdog = stack.enter_context(
                Watchdog(
                    self.timeout, partial(self._on_beacon_timed_out, beacon)
                ).use_soon(nursery)
            )

            stack.enter_context(overridden(self, _watchdog=watchdog))

            yield

    def _on_beacon_timed_out(self, beacon: "Beacon") -> None:
        """Handler called by the watchdog when the beacon did not receive an
        updated position in time.
        """
        beacon.update_status(active=False)

    def _on_rtk_packet_received(self, sender, packet, beacon: "Beacon") -> None:
        """Handler called for each incoming RTK packet if this extension is
        interested in it. The packet is used to update the position of the
        RTK beacon.
        """
        try:
            for packet in self._parser(packet):
                if isinstance(packet, RTCMV3StationaryAntennaPacket):
                    beacon.update_status(
                        position=self._trans.to_gps(packet.position),  # type: ignore
                        active=True,
                    )
                    if self._watchdog:
                        self._watchdog.notify()
        except Exception:
            # Parse error, ignore
            pass
