from pytest import raises
from pytest_trio import trio_fixture
from trio import WouldBlock, move_on_after, sleep

from flockwave.server.concurrency import AsyncBundler, Future, FutureCancelled


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


class TestFuture:
    def test_future_base_state(self):
        future = Future()

        assert not future.cancelled()
        assert not future.done()
        with raises(WouldBlock):
            future.result()
        with raises(WouldBlock):
            future.exception()

    async def test_resolution_with_value(self, nursery):
        future = Future()

        async def resolver():
            future.set_result(42)

        nursery.start_soon(resolver)
        assert await future.wait() == 42

        assert not future.cancelled()
        assert future.done()
        assert future.result() == 42
        assert future.exception() is None

    async def test_resolution_with_value_twice(self, nursery):
        future = Future()

        async def resolver(task_status):
            future.set_result(42)
            task_status.started()

        await nursery.start(resolver)

        with raises(RuntimeError):
            await nursery.start(resolver)

    async def test_resolution_with_exception(self, nursery):
        future = Future()

        async def resolver():
            future.set_exception(ValueError("test"))

        nursery.start_soon(resolver)
        with raises(ValueError):
            await future.wait()

        assert not future.cancelled()
        assert future.done()
        assert isinstance(future.exception(), ValueError)
        assert "test" in str(future.exception())

        with raises(ValueError):
            future.result()

    async def test_cancellation(self, nursery):
        future = Future()

        async def resolver(task_status):
            future.cancel()
            task_status.started()

        await nursery.start(resolver)

        assert future.cancelled()
        assert future.done()

        with raises(FutureCancelled):
            await future.wait()

        with raises(FutureCancelled):
            await future.result()

        with raises(FutureCancelled):
            await future.exception()

    async def test_trio_cancellation(self, autojump_clock, nursery):
        future = Future()

        async def resolver():
            await sleep(10)
            future.cancel()

        nursery.start_soon(resolver)
        with move_on_after(5) as scope:
            await future.wait()

        # At this point, the await was cancelled but the future is still
        # running
        assert scope.cancelled_caught

        assert not future.cancelled()
        assert not future.done()

        with raises(FutureCancelled):
            await future.wait()

        assert future.done()
        assert future.cancelled()
