from pytest_trio import trio_fixture
from trio import sleep

from flockwave.server.message_hub import BatchMessageRateLimiter, UAVMessageRateLimiter


@trio_fixture
def create_rate_limiter(nursery):
    def rate_limiter_factory(cls, *args, **kwds):
        result = []

        async def dispatcher(message):
            result.append(message)

        rate_limiter = cls(*args, **kwds)
        nursery.start_soon(rate_limiter.run, dispatcher, nursery)
        return rate_limiter, result

    yield rate_limiter_factory


def create_message(uav_ids):
    return tuple(uav_ids)


class TestBatchMessageRateLimiter:
    async def test_yields_nothing_by_default(self, create_rate_limiter, autojump_clock):
        _, result = create_rate_limiter(
            BatchMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )
        assert result == []

    async def test_yields_correctly_without_batching(
        self, create_rate_limiter, autojump_clock
    ):
        rate_limiter, result = create_rate_limiter(
            BatchMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )
        await sleep(0.1)  # let the nursery start the rate limiter
        rate_limiter.add_request(1)
        await sleep(1)
        rate_limiter.add_request(2)
        rate_limiter.add_request(3)
        rate_limiter.add_request(4)
        await sleep(1)

        assert result == [(1,), (2, 3, 4)]

    async def test_yields_correctly_with_batching(
        self, create_rate_limiter, autojump_clock
    ):
        rate_limiter, result = create_rate_limiter(
            BatchMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )

        await sleep(0.1)  # let the nursery start the rate limiter

        # batch 1 - this is always sent immediately ###
        rate_limiter.add_request(1)

        # batch 2 - gets delayed because batch 1 went out recently ###
        await sleep(0.05)
        rate_limiter.add_request(1)
        rate_limiter.add_request(3)
        rate_limiter.add_request(4)
        await sleep(0.01)
        rate_limiter.add_request(1)
        await sleep(0.02)
        rate_limiter.add_request(2)
        rate_limiter.add_request(4)
        await sleep(1)

        # batch 3 - this is sent immediately because there was 1s of silence ###
        rate_limiter.add_request(3)
        await sleep(0.05)

        # batch 4 - gets delayed because batch 3 went out recently ###
        rate_limiter.add_request(3)
        rate_limiter.add_request(4)
        await sleep(0.03)
        rate_limiter.add_request(6)
        await sleep(1)

        assert result == [(1,), (1, 2, 3, 4), (3,), (3, 4, 6)]


class TestUAVMessageRateLimiter:
    async def test_yields_nothing_by_default(self, create_rate_limiter, autojump_clock):
        _, result = create_rate_limiter(
            UAVMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )
        assert result == []

    async def test_yields_correctly_without_batching(
        self, create_rate_limiter, autojump_clock
    ):
        rate_limiter, result = create_rate_limiter(
            UAVMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )
        await sleep(0.1)  # let the nursery start the rate limiter
        rate_limiter.add_request((1, 2))
        await sleep(1)
        rate_limiter.add_request((3, 4, 5))
        await sleep(1)

        assert result == [(1, 2), (3, 4, 5)]

    async def test_yields_correctly_with_batching(
        self, create_rate_limiter, autojump_clock
    ):
        rate_limiter, result = create_rate_limiter(
            UAVMessageRateLimiter, name="Test", factory=create_message, delay=0.1
        )

        await sleep(0.1)  # let the nursery start the rate limiter

        # batch 1 - this is always sent immediately ###
        rate_limiter.add_request((1, 2))

        # batch 2 - gets delayed because batch 1 went out recently ###
        await sleep(0.05)
        rate_limiter.add_request((1, 3, 4))
        await sleep(0.01)
        rate_limiter.add_request((1,))
        await sleep(0.02)
        rate_limiter.add_request((2, 4))
        await sleep(1)

        # batch 3 - this is sent immediately because there was 1s of silence ###
        rate_limiter.add_request((3, 4, 5))
        await sleep(0.05)

        # batch 4 - gets delayed because batch 3 went out recently ###
        rate_limiter.add_request((3, 4))
        await sleep(0.03)
        rate_limiter.add_request((6,))
        await sleep(1)

        assert result == [(1, 2), (1, 2, 3, 4), (3, 4, 5), (3, 4, 6)]
