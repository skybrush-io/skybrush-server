"""Extension that watches the USB bus of the computer the server is running
on and emits a signal whenever a new USB device is plugged in or an existing
USB device is removed.
"""

from __future__ import annotations

from contextlib import aclosing
from typing import TYPE_CHECKING

from aio_usb_hotplug import HotplugDetector, NoBackendError

from flockwave.server.ext.signals import SignalsExtensionAPI

if TYPE_CHECKING:
    from logging import Logger
    from flockwave.server.app import SkybrushServer


async def run(app: SkybrushServer, configuration, log: Logger):
    signal = app.import_api("signals", SignalsExtensionAPI).get("hotplug:event")

    try:
        gen = HotplugDetector().events()
        async with aclosing(gen):
            async for event in gen:
                signal.send(event=event)
    except NoBackendError:
        log.warning("No suitable backend found for scanning the USB bus")
        # TODO(ntamas):add hints about what to do. On macOS, one needs to
        # install libusb from Homebrew, and add /opt/homebrew/lib to
        # the DYLD_LIBRARY_PATH


description = "Hotplug event provider for other extensions"
schema = {}
