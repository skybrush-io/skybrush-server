"""Extension that watches the USB bus of the computer the server is running
on and emits a signal whenever a new USB device is plugged in or an existing
USB device is removed.
"""

from aio_usb_hotplug import HotplugDetector

from flockwave.server.concurrency import aclosing


async def run(app):
    signal = app.import_api("signals").get("hotplug:event")

    gen = HotplugDetector().events()
    async with aclosing(gen):
        async for event in gen:
            signal.send(event=event)
