from pytest import raises
from pytest_trio import trio_fixture
from trio import move_on_after, sleep

from flockwave.server.concurrency import AsyncBundler


@trio_fixture
def bundler():
    bundler = AsyncBundler()
    with move_on_after(10):
        yield bundler


class TestAsyncBundler:
    async def test_yields_nothing_when_empty(self, bundler, autojump_clock):
        async for bundle in bundler:
            assert False, "bundler should not yield any bundles"

    async def test_yields_all_items_after_add(self, bundler, autojump_clock):
        bundler.add(2)
        bundler.add(3)
        bundler.add(5)
        async for bundle in bundler:
            assert bundle == set([2, 3, 5])

    async def test_yields_all_items_after_add_many(self, bundler, autojump_clock):
        bundler.add_many([2, 3, 5, 7])
        bundler.add_many((11, 13))
        async for bundle in bundler:
            assert bundle == set([2, 3, 5, 7, 11, 13])

    async def test_clears_items_after_yielding(self, bundler, autojump_clock):
        bundler.add_many([2, 3, 5, 7])
        async for bundle in bundler:
            assert bundle == set([2, 3, 5, 7])
            break
        bundler.add_many((11, 13))
        async for bundle in bundler:
            assert bundle == set([11, 13])

    async def test_clears_items_before_yielding(self, bundler, autojump_clock):
        bundler.add_many([2, 3, 5, 7])
        bundler.clear()
        async for bundle in bundler:
            assert bundle == set()
            break

        bundler.add_many([2, 3, 5, 7])
        bundler.clear()
        bundler.add_many([11, 13])
        async for bundle in bundler:
            assert bundle == set([11, 13])

    async def test_filters_duplicates(self, bundler, autojump_clock):
        bundler.add_many([2, 3, 3, 5, 5, 5, 7])
        async for bundle in bundler:
            assert bundle == set([2, 3, 5, 7])
            break
        bundler.add_many((2, 2, 3, 11))
        async for bundle in bundler:
            assert bundle == set([2, 3, 11])

    async def test_separated_producer_consumer(self, bundler, autojump_clock, nursery):
        async def producer():
            items = list(range(10))
            for item in items:
                bundler.add(item)
                await sleep(0.21)

        async def consumer():
            bundles = []
            async for bundle in bundler:
                bundles.append(bundle)
                await sleep(0.5)
            return bundles

        nursery.start_soon(producer)
        bundles = await consumer()

        assert len(bundles) == 4
        assert bundles[0] == (0, 1, 2)
        assert bundles[1] == (3, 4)
        assert bundles[2] == (5, 6, 7)
        assert bundles[3] == (8, 9)

    async def test_multiple_consumers(self, bundler, autojump_clock, nursery):
        async def consumer():
            return [bundle async for bundle in bundler]

        nursery.start_soon(consumer)
        await sleep(0.02)

        with raises(RuntimeError) as ex:
            await consumer()

        assert "can only have one listener" in str(ex.value)
