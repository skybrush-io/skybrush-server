from pytest import fixture
from trio import sleep

from flockwave.server.tasks.led_lights import (
    LEDLightConfigurationManagerBase,
    LightConfiguration,
)


class MockLEDLightConfigurationManager(LEDLightConfigurationManagerBase[int]):
    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.counter = 0
        self.configs_seen = []
        self.messages_sent = []

    def _create_light_control_packet(self, config) -> int:
        self.configs_seen.append(config)
        self.counter += 1
        return self.counter - 1

    async def _send_light_control_packet(self, packet: int) -> None:
        self.messages_sent.append(packet)
        await sleep(0)


@fixture
def manager(nursery) -> MockLEDLightConfigurationManager:
    manager = MockLEDLightConfigurationManager()
    nursery.start_soon(manager.run)
    return manager


def off() -> LightConfiguration:
    return LightConfiguration.turn_off()


def solid(red: int, green: int, blue: int) -> LightConfiguration:
    return LightConfiguration.create_solid_color((red, green, blue))


async def test_no_trigger(manager: MockLEDLightConfigurationManager, autojump_clock):
    assert (
        manager.counter == 0 and not manager.configs_seen and not manager.messages_sent
    )
    await sleep(100)
    assert (
        manager.counter == 0 and not manager.configs_seen and not manager.messages_sent
    )


async def test_single_trigger(
    manager: MockLEDLightConfigurationManager, autojump_clock
):
    config = solid(255, 0, 0)
    manager.notify_config_changed(config)
    manager.reset()
    await sleep(1.1)

    # Rapid mode sends messages every 0.2 seconds by default
    assert manager.configs_seen == [config] * 6
    assert manager.messages_sent == list(range(6))

    # Duration of rapid mode is 5 seconds, we are at T=1.1 so wait for 4s
    await sleep(4)
    assert manager.configs_seen == [config] * 26
    assert manager.messages_sent == list(range(26))
    manager.reset()

    # We should have now reverted to non-rapid mode, sending messages every
    # 3s only
    await sleep(9)
    assert manager.configs_seen == [config] * 3
    assert manager.messages_sent == list(range(3))

    # Send another message to turn off the lights
    config = off()
    manager.reset()
    manager.notify_config_changed(config)
    await sleep(0.9)

    # Rapid mode was triggered again and it sends messages every 0.2 seconds
    assert manager.configs_seen == [config] * 5
    assert manager.messages_sent == list(range(5))

    # Duration of rapid mode is 5 seconds again
    await sleep(4.2)
    assert manager.configs_seen == [config] * 26
    assert manager.messages_sent == list(range(26))
    manager.reset()

    # We should have now reverted to non-rapid mode, but since we are in the
    # "off" stage, we won't send messages any more, only one last message after
    # the rapid burst
    await sleep(200)
    assert manager.configs_seen == [config]
    assert manager.messages_sent == [0]
