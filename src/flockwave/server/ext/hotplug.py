"""Extension that watches the USB bus of the computer the server is running
on and emits a signal whenever a new USB device is plugged in or an existing
USB device is removed.
"""

from aio_usb_hotplug import HotplugDetector, NoBackendError

from flockwave.concurrency import aclosing


async def run(app, configuration, log):
    signal = app.import_api("signals").get("hotplug:event")

    try:
        gen = HotplugDetector().events()
        async with aclosing(gen):
            async for event in gen:
                signal.send(event=event)
    except NoBackendError:
        log.warn("No suitable backend found for scanning the USB bus")
        # TODO(ntamas):add hints about what to do. On macOS, one needs to
        # install libusb from Homebrew, and add /opt/homebrew/lib to
        # the DYLD_LIBRARY_PATH


description = "Hotplug event provider for other extensions"
schema = {}
