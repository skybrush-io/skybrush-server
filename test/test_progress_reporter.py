from contextlib import closing
from pytest import raises
from trio import sleep, TooSlowError
from typing import Optional

from flockwave.server.tasks import ProgressReporter


async def test_progress_reporter(nursery, autojump_clock):
    reporter = ProgressReporter()

    async def generator():
        with closing(reporter):
            reporter.notify(5, "foo")
            await sleep(1)
            reporter.notify(10)
            await sleep(1)
            reporter.notify(15)
            reporter.notify(20)
            reporter.notify(25)
            await sleep(1)
            reporter.notify(message="bar")
            reporter.notify(30)
            await sleep(1)
            reporter.notify()
            await sleep(1)
            reporter.notify(message="")
            await sleep(1)
            reporter.notify(100)

    nursery.start_soon(generator)

    expected_seq: list[tuple[Optional[int], Optional[str]]] = [
        (5, "foo"),
        (10, "foo"),
        (25, "foo"),
        (30, "bar"),
        (30, "bar"),
        (30, ""),
        (100, ""),
    ]
    index = 0

    async for progress in reporter.updates():
        assert index < len(expected_seq), "reporter yielded more items than expected"

        percentage, message = expected_seq[index]
        assert progress.percentage == percentage
        assert progress.message == message

        index += 1

    assert index == len(expected_seq)
    assert reporter.done

    async for _ in reporter.updates():
        raise AssertionError("should not ever get here")

    assert reporter.done


async def test_progress_reporter_auto_close(nursery, autojump_clock):
    reporter = ProgressReporter(auto_close=True)

    async def generator():
        reporter.notify(5, "foo")
        await sleep(1)
        reporter.notify(100)

    nursery.start_soon(generator)

    expected_seq: list[tuple[Optional[int], Optional[str]]] = [
        (5, "foo"),
        (100, "foo"),
    ]
    index = 0

    async for progress in reporter.updates():
        assert index < len(expected_seq), "reporter yielded more items than expected"

        percentage, message = expected_seq[index]
        assert progress.percentage == percentage
        assert progress.message == message

        index += 1

    assert index == len(expected_seq)
    assert reporter.done


async def test_progress_reporter_timeout(nursery, autojump_clock):
    reporter = ProgressReporter()

    async def generator():
        with closing(reporter):
            reporter.notify(5, "foo")
            await sleep(1)
            reporter.notify(10)
            await sleep(10)
            reporter.notify(100)

    nursery.start_soon(generator)

    expected_seq: list[tuple[Optional[int], Optional[str]]] = [
        (5, "foo"),
        (10, "foo"),
    ]
    index = 0

    async for progress in reporter.updates(timeout=5):
        assert index < len(expected_seq), "reporter yielded more items than expected"

        percentage, message = expected_seq[index]
        assert progress.percentage == percentage
        assert progress.message == message

        index += 1

        if index == len(expected_seq):
            # Now we should have a timeout in the next iteration
            break

    assert not reporter.done

    with raises(TooSlowError):
        async for _ in reporter.updates(timeout=5):
            pass


async def test_progress_reporter_timeout_no_failure(nursery, autojump_clock):
    reporter = ProgressReporter()

    async def generator():
        with closing(reporter):
            reporter.notify(5, "foo")
            await sleep(1)
            reporter.notify(10)
            await sleep(10)
            reporter.notify(100)

    nursery.start_soon(generator)

    expected_seq: list[tuple[Optional[int], Optional[str]]] = [
        (5, "foo"),
        (10, "foo"),
    ]
    index = 0

    async for progress in reporter.updates(timeout=5, fail_on_timeout=False):
        assert index < len(expected_seq), "reporter yielded more items than expected"

        percentage, message = expected_seq[index]
        assert progress.percentage == percentage
        assert progress.message == message

        index += 1

    assert index == len(expected_seq)
    assert reporter.done
