from contextlib import closing
from functools import partial
from time import sleep

import trio.to_thread

from flockwave.server.model.commands import Progress
from flockwave.server.tasks.progress import ProgressReporter


async def test_progress_reporter(nursery, autojump_clock):
    reporter = ProgressReporter.for_thread()

    microsleep = partial(sleep, 0.001)

    def generator():
        with closing(reporter):
            reporter.notify(5, "foo")
            microsleep()
            reporter.notify(10)
            microsleep()
            reporter.notify(15)
            reporter.notify(20)
            reporter.notify(25)
            microsleep()
            reporter.notify(message="bar")
            reporter.notify(30)
            microsleep()
            reporter.notify()
            microsleep()
            reporter.notify(message="")
            microsleep()
            reporter.notify(100)

    nursery.start_soon(trio.to_thread.run_sync, generator)

    expected_seq_loose: list[tuple[int | None, str | None]] = [
        (5, "foo"),
        (10, "foo"),
        (25, "foo"),
        (30, "bar"),
        (30, ""),
        (100, ""),
    ]
    index = 0

    # We can compare the expected sequence with the actual sequence only in a loose
    # sense because inter-thread timings are not predictable. We maintain an index
    # in `expected_seq_loose` and accept the next item yielded from the updates if it
    # comes anywhere from that index all the way up to the end of the sequence.
    async for progress in reporter.updates():
        assert index < len(expected_seq_loose), (
            "reporter yielded more items than expected"
        )
        assert isinstance(progress, Progress)

        try:
            index = expected_seq_loose.index(
                (progress.percentage, progress.message), index
            )
        except ValueError:
            raise AssertionError(
                f"reporter yielded unexpected item {progress!r} at index {index}"
            ) from None

    assert index == len(expected_seq_loose) - 1
    assert (progress.percentage, progress.message) == expected_seq_loose[-1]
    assert reporter.done

    async for _ in reporter.updates():
        raise AssertionError("should not ever get here")

    assert reporter.done
