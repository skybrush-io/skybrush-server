"""Class responsible for spinning up and stopping workers as needed."""

from subprocess import PIPE, STDOUT
from trio import move_on_after, Nursery, open_process, Process, sleep_forever

from .errors import NoIdleWorkerError
from .logger import log as base_log

__all__ = ("WorkerManager",)

log = base_log.getChild("workers")


class WorkerManager:
    """Class responsible for spinning up and stopping workers as needed."""

    def __init__(self, max_count: int = 1):
        """Constructor.

        Parameters:
            max_workers: maximum number of workers allowed to run concurrently
        """
        self._nursery = None
        self._processes = [None] * max_count

    @property
    def max_count(self) -> int:
        """Returns the maximum number of worker processes supported by the
        worker manager.
        """
        return len(self._processes)

    @max_count.setter
    def max_count(self, value: int) -> None:
        if value < self.max_count:
            raise NotImplementedError(
                "decreasing the number of workers is not implemented yet"
            )

        self._processes += [None] * (value - self.max_count)

    async def request_worker(self) -> int:
        """Requests the worker manager to spin up a new worker and then
        return the port that the worker will be listening on.

        Returns:
            the port that the worker will be listening on

        Raises:
            NoIdleWorkerError: when there aren't any idle workers available
        """
        index = self._find_vacant_slot()
        if index is None:
            raise NoIdleWorkerError("No idle worker available")

        log.info(f"Launching new worker in slot {index}")

        self._processes[index] = "starting"
        with move_on_after(10) as cancel_scope:
            process = await open_process(
                ["python", "test.py"], stdout=PIPE, stderr=STDOUT, bufsize=0
            )

        if cancel_scope.cancelled_caught:
            self._processes[index] = None
        else:
            self._processes[index] = process
            self._nursery.start_soon(self._stream_process_output, index, process)
            self._nursery.start_soon(self._supervise_process, index, process)

    async def run(self, nursery: Nursery) -> None:
        try:
            self._nursery = nursery
            await sleep_forever()
        finally:
            self._nursery = None

    def _find_vacant_slot(self) -> int:
        for index, slot in enumerate(self._processes):
            if slot is None:
                return index
        return None

    async def _stream_process_output(self, index: int, process: Process) -> None:
        logger = log.getChild(f"worker{index}")
        try:
            chunks = []
            while True:
                chunk = await process.stdout.receive_some()
                while chunk:
                    first, sep, chunk = chunk.partition(b"\n")
                    chunks.append(first)
                    if sep:
                        logger.info(
                            b"".join(chunks).decode("utf-8", "backslashreplace")
                        )
                        del chunks[:]

        except Exception as ex:
            log.error(f"Worker #{index} stdout streaming stopped.")
            log.exception(ex)

    async def _supervise_process(self, index: int, process: Process) -> None:
        log.info(f"Worker #{index} (PID={process.pid}) started")
        try:
            code = await process.wait()
            if code:
                log.warn(f"Worker #{index} (PID={process.pid}) exited with code {code}")
            else:
                log.info(f"Worker #{index} (PID={process.pid}) exited.")
        except Exception as ex:
            log.error(f"Worker #{index} exited with an exception.")
            log.exception(ex)
        finally:
            self._processes[index] = None
