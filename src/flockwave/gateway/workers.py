"""Class responsible for spinning up and stopping workers as needed."""

import sys

from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE, STDOUT
from tempfile import NamedTemporaryFile
from trio import move_on_after, open_nursery, open_process, Process, sleep_forever
from typing import Any, Callable, IO, Optional

from .errors import NoIdleWorkerError
from .logger import log as base_log

__all__ = ("WorkerManager",)

log = base_log.getChild("workers")


@dataclass
class WorkerEntry:
    id: str
    name: Optional[str] = None
    process: Optional[Process] = None
    starting: bool = True
    config_fp: Optional[IO[bytes]] = None

    def assign_process(self, process: Process) -> None:
        self.process = process
        self.starting = False

    def remove_configuration_file_if_needed(self):
        if self.config_fp is not None:
            Path(self.config_fp.name).unlink()

    async def terminate(self):
        if self.process:
            with move_on_after(3) as cancel_scope:
                self.process.terminate()
                await self.process.wait()
            if cancel_scope.cancelled_caught:
                self.process.kill()


class WorkerManager:
    """Class responsible for spinning up and stopping workers as needed."""

    def __init__(
        self, max_count: int = 1, worker_config_factory: Callable[[int], Any] = None
    ):
        """Constructor.

        Parameters:
            max_workers: maximum number of workers allowed to run concurrently
        """
        self._nursery = None

        self._processes = [None] * max_count
        self._users_to_entries = {}

        self.worker_config_factory = worker_config_factory

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

    async def request_worker(self, id, name) -> int:
        """Requests the worker manager to spin up a new worker and then
        return the port that the worker will be listening on.

        May cancel existing workers registered under the same ID to ensure that
        a user has only one worker.

        Parameters:
            id: ID of the user who is requesting a new worker. Used to ensure
                that there is only one worker running for a given user. Existing
                workers belonging to the same user will be terminated.
            name: username or a human-readable identifier of the user who is
                requesting a new worker. Used only in logging messages.

        Returns:
            the index of the worker that was assigned to the user ID

        Raises:
            NoIdleWorkerError: when there aren't any idle workers available
        """
        user = f"{name} (id={id})" if name else id
        entry = self._users_to_entries.get(id)
        if entry:
            log.info(f"Terminating existing process of user {user}")
            await entry.terminate()
            try:
                index = self._processes.index(entry)
                self._processes[index] = None
                if self._users_to_entries[entry.id] is entry:
                    del self._users_to_entries[id]
            except ValueError:
                pass

        index = self._find_vacant_slot()
        if index is None:
            raise NoIdleWorkerError("No idle worker available")

        log.info(f"Launching new worker for user {user} in slot {index}")

        self._processes[index] = entry = WorkerEntry(id=str(id), name=name)
        self._users_to_entries[id] = entry

        if self.worker_config_factory:
            config = self.worker_config_factory(index)
        else:
            config = {}

        entry.config_fp = NamedTemporaryFile(
            mode="w+", encoding="utf-8", suffix=".cfg", delete=False
        )
        try:
            with entry.config_fp as fp:
                for key, value in config.items():
                    fp.write(f"{key} = {value!r}\n")
            with move_on_after(10) as cancel_scope:
                process = await open_process(
                    [
                        sys.executable,
                        "-m",
                        "flockwave.server.launcher",
                        "--log-style=plain",
                        "-c",
                        entry.config_fp.name,
                    ],
                    stdout=PIPE,
                    stderr=STDOUT,
                    cwd=str(Path(__file__).parent.parent.parent),
                )

            if cancel_scope.cancelled_caught:
                self._processes[index] = None
                if self._users_to_entries.get(id) is entry:
                    del self._users_to_entries[id]
            else:
                self._nursery.start_soon(self._stream_process_output, index, process)
                self._nursery.start_soon(self._supervise_process, index, entry)
                entry.assign_process(process)

            return index
        finally:
            if entry.process is None:
                # Process was not started in the end so remove the temporary file
                entry.remove_configuration_file_if_needed()

    async def run(self) -> None:
        async with open_nursery() as nursery:
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
                if not chunk:
                    break
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

    async def _supervise_process(self, index: int, entry: WorkerEntry) -> None:
        process = entry.process
        user = entry.id
        worker = f"Worker #{index} (user={user}, PID={process.pid})"

        log.info(f"{worker} started")
        try:
            code = await process.wait()
            if code:
                log.warn(f"{worker} exited with code {code}")
            else:
                log.info(f"{worker} exited.")
        except Exception as ex:
            log.error(f"{worker} exited with an exception.")
            log.exception(ex)
        finally:
            if self._processes[index] is entry:
                self._processes[index] = None

            if self._users_to_entries.get(entry.id) is entry:
                del self._users_to_entries[entry.id]

            entry.remove_configuration_file_if_needed()
