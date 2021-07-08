"""Extension that implements an OSC client in Skybrush that forward the
positions of the drones to a remote OSC target.
"""

from trio import sleep_forever


async def run(app, configuration, log):
    log.info("OSC extension running")
    await sleep_forever()
