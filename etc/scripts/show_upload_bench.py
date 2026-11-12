#!/usr/bin/env python3
"""Bench test for show uploads followed by sleep mode.

The script reproduces the protocol used before a show: upload the show, put
the drone to sleep, wake it up later. After each wake-up it asks the drone for
the CRC32 of its show file to detect whether the file survived the power cut
of the flight controller.

The script talks to a running Skybrush Server over its TCP extension, the same
way as Skybrush Live does. Every cycle exercises the production code path:
``__show_upload``, MAVFTP, ``UAV-SLEEP`` and ``UAV-WAKEUP``. The CRC is read
back with the hidden ``__file_crc`` command.

Run it from the root of the repository with ``uv run``.

Subcommands:

``make-spec``
    Builds a per-drone show specification from a ``.skyc`` file and a site
    file, optionally with a second variant (B) whose trajectory is shifted
    upwards. Example site file::

        {
            "coordinateSystem": {
                "origin": [18.915125, 47.486305],
                "orientation": "59",
                "type": "neu"
            },
            "amslReference": null,
            "geofence": null
        }

    Note that ``origin`` is in longitude-latitude order.

``run``
    Runs the upload-sleep-wakeup cycles and appends one JSON line per cycle
    to the results file. The run resumes from an existing results file.
    With ``-v``, it also prints each step of a cycle while it happens.

``summary``
    Prints the outcome counts per test setting from a results file.

Outcomes of a cycle:

- ``OK``: the show file after wake-up is the one that was uploaded.
- ``ROLLBACK``: the show file after wake-up is the previously uploaded one.
- ``CORRUPT``: the show file after wake-up is neither, or it is missing.
- ``UPLOAD_MISMATCH``: the upload reported success, but the CRC read back
  before sleep belongs to the other show.
- ``INFRA``: the upload, the sleep, the wake-up or the CRC query failed. These
  cycles do not count towards the statistics.

The run stops at the first ``ROLLBACK``, ``CORRUPT`` or ``UPLOAD_MISMATCH``
and leaves the drone untouched. Power the drone off and image its SD card
with ``dd`` before doing anything else with it.

Example::

    uv run python etc/scripts/show_upload_bench.py make-spec \\
        --skyc show.skyc --drone 1 --site site.json \\
        --out a.json --derive-b b.json
    uv run python etc/scripts/show_upload_bench.py run \\
        --uav-id 10 --show-a a.json --show-b b.json \\
        --delay 10 --cancels 2 --cycles 500 --tag "loss=15%" \\
        --out results.jsonl
    uv run python etc/scripts/show_upload_bench.py summary results.jsonl
"""

from __future__ import annotations

import json
import posixpath
import sys
from argparse import ArgumentParser, Namespace
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from random import uniform
from time import monotonic
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

import trio

SHOW_FILE_PATH = "/collmot/show.skyb"
"""Path of the show file on the drone."""

SLEEPING_ERROR_CODE = 9
"""Flockwave error code that the server reports while the drone is sleeping
(``FlockwaveErrorCode.SLEEPING``)."""

FAILURES = ("ROLLBACK", "CORRUPT", "UPLOAD_MISMATCH")
"""Outcomes that stop the run unless ``--keep-going`` is given."""


# ---------------------------------------------------------------------------
# Flockwave client
# ---------------------------------------------------------------------------


class CommandError(RuntimeError):
    """Error returned by the server for a request or an async command."""


class BenchAbort(RuntimeError):
    """Error that stops the bench test before or between cycles."""


class _Slot:
    """Single-use container for a value that arrives from the reader task."""

    def __init__(self):
        self._event = trio.Event()
        self._value: Any = None

    def set(self, value: Any) -> None:
        if not self._event.is_set():
            self._value = value
            self._event.set()

    async def wait(self) -> Any:
        await self._event.wait()
        return self._value


class FlockwaveClient:
    """Minimal Flockwave client over a TCP connection.

    Messages are newline-delimited JSON objects. Responses refer to the ID of
    the request in their ``refs`` field. Results of asynchronous commands
    arrive later in ``ASYNC-RESP`` or ``ASYNC-TIMEOUT`` notifications.
    """

    def __init__(self, stream: trio.SocketStream):
        self._stream = stream
        self._responses: dict[str, _Slot] = {}
        self._async_results: dict[str, _Slot] = {}
        self._send_lock = trio.Lock()

    @classmethod
    @asynccontextmanager
    async def connect(cls, host: str, port: int) -> AsyncGenerator[FlockwaveClient]:
        stream = await trio.open_tcp_stream(host, port)
        async with stream, trio.open_nursery() as nursery:
            client = cls(stream)
            nursery.start_soon(client._read_messages)
            try:
                yield client
            finally:
                nursery.cancel_scope.cancel()

    async def request(self, body: dict[str, Any], timeout: float = 30) -> Any:
        """Sends a request and returns the body of the response."""
        message_id = uuid4().hex
        slot = self._responses[message_id] = _Slot()
        try:
            await self._send({"$fw.version": "1.0", "id": message_id, "body": body})
            with trio.fail_after(timeout):
                return await slot.wait()
        except trio.TooSlowError:
            raise CommandError(f"No response to {body['type']} in time") from None
        finally:
            self._responses.pop(message_id, None)

    async def start_uav_request(
        self, type: str, uav_id: str, **fields: Any
    ) -> tuple[str | None, Any]:
        """Sends a request that targets a single UAV.

        Returns:
            a pair of a receipt ID and a result. The receipt ID is ``None`` if
            the server returned the result immediately. Otherwise the result
            must be retrieved with `wait_for_receipt()`.
        """
        body = await self.request({"type": type, "ids": [uav_id], **fields})

        error = (body.get("error") or {}).get(uav_id)
        if error is not None:
            raise CommandError(str(error))

        receipt = (body.get("receipt") or {}).get(uav_id)
        if receipt is not None:
            return receipt, None

        return None, (body.get("result") or {}).get(uav_id)

    async def wait_for_receipt(self, receipt: str, timeout: float) -> Any:
        """Waits for the result of an asynchronous command."""
        try:
            with trio.fail_after(timeout):
                body = await self._async_slot(receipt).wait()
        except trio.TooSlowError:
            raise CommandError("No result for async command in time") from None
        finally:
            self._async_results.pop(receipt, None)

        if "error" in body:
            raise CommandError(str(body["error"]))
        return body.get("result")

    async def run_uav_request(
        self, type: str, uav_id: str, *, timeout: float, **fields: Any
    ) -> Any:
        """Sends a request that targets a single UAV and waits for its result,
        even if the command is executed asynchronously.
        """
        receipt, result = await self.start_uav_request(type, uav_id, **fields)
        if receipt is not None:
            result = await self.wait_for_receipt(receipt, timeout)
        return result

    async def run_uav_command(
        self, uav_id: str, command: str, *, timeout: float, **kwds: Any
    ) -> Any:
        """Executes a command on a single UAV with OBJ-CMD."""
        return await self.run_uav_request(
            "OBJ-CMD", uav_id, timeout=timeout, command=command, args=[], kwds=kwds
        )

    async def cancel(self, receipt: str) -> None:
        """Cancels an asynchronous command."""
        await self.request({"type": "ASYNC-CANCEL", "ids": [receipt]})

    async def get_uav_errors(self, uav_id: str) -> set[int]:
        """Returns the error codes in the current status of a UAV."""
        body = await self.request({"type": "UAV-INF", "ids": [uav_id]})
        status = (body.get("status") or {}).get(uav_id)
        if status is None:
            error = (body.get("error") or {}).get(uav_id, "no status")
            raise CommandError(f"Cannot query UAV {uav_id}: {error}")
        return set(status.get("errors") or ())

    def _async_slot(self, receipt: str) -> _Slot:
        # The ASYNC-RESP may arrive before the caller starts waiting for it
        slot = self._async_results.get(receipt)
        if slot is None:
            slot = self._async_results[receipt] = _Slot()
        return slot

    def _handle_message(self, message: dict[str, Any]) -> None:
        body = message.get("body") or {}

        refs = message.get("refs")
        if refs is not None:
            slot = self._responses.get(refs)
            if slot is not None:
                slot.set(body)
            return

        type = body.get("type")
        if type == "ASYNC-RESP":
            self._async_slot(body["id"]).set(body)
        elif type == "ASYNC-TIMEOUT":
            for receipt in body.get("ids") or ():
                self._async_slot(receipt).set(
                    {"error": "Command timed out or was cancelled on the server"}
                )

    async def _read_messages(self) -> None:
        buffer = b""
        while True:
            data = await self._stream.receive_some(65536)
            if not data:
                raise ConnectionError("Server closed the connection")

            buffer += data
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                if line.strip():
                    self._handle_message(json.loads(line))

    async def _send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message).encode("utf-8") + b"\n"
        async with self._send_lock:
            await self._stream.send_all(data)


# ---------------------------------------------------------------------------
# make-spec
# ---------------------------------------------------------------------------


def _load_drone_settings_from_skyc(path: Path, drone: str) -> dict[str, Any]:
    """Loads the settings of a single drone from a ``.skyc`` file and resolves
    the ``$ref`` entries that point to other files in the archive.

    Args:
        path: path of the ``.skyc`` file
        drone: name of the drone (e.g., ``Drone 10``) or its 1-based index
    """
    with ZipFile(path) as archive:
        show = json.loads(archive.read("show.json"))
        drones = show["swarm"]["drones"]

        if drone.isdigit():
            index = int(drone) - 1
            if not 0 <= index < len(drones):
                raise SystemExit(f"Drone index {drone} is out of range")
            entry = drones[index]
        else:
            matches = [d for d in drones if d["settings"].get("name") == drone]
            if not matches:
                raise SystemExit(f"No drone named {drone!r} in {path}")
            entry = matches[0]

        def resolve(value: Any) -> Any:
            if isinstance(value, dict) and "$ref" in value:
                ref = value["$ref"].partition("#")[0]
                return json.loads(archive.read(posixpath.normpath(ref)))
            return value

        return {key: resolve(value) for key, value in entry["settings"].items()}


def _shift_trajectory(trajectory: dict[str, Any], dz: float) -> dict[str, Any]:
    """Returns a copy of the trajectory with all points and control points
    shifted vertically by the given amount.
    """
    result = deepcopy(trajectory)
    for point in result["points"]:
        _, position, control_points = point
        position[2] += dz
        for control_point in control_points:
            control_point[2] += dz
    return result


def make_spec(args: Namespace) -> None:
    site = json.loads(Path(args.site).read_text())
    coordinate_system = site.get("coordinateSystem")
    if (
        not isinstance(coordinate_system, dict)
        or not {
            "origin",
            "orientation",
            "type",
        }
        <= coordinate_system.keys()
    ):
        raise SystemExit(
            "The site file needs a coordinateSystem with origin, orientation and type"
        )

    settings = _load_drone_settings_from_skyc(Path(args.skyc), args.drone)
    if "trajectory" not in settings:
        raise SystemExit("The selected drone has no trajectory")

    spec: dict[str, Any] = {
        "coordinateSystem": coordinate_system,
        "mission": {"id": "bench-A", "title": "Show upload bench test", "numDrones": 1},
        "trajectory": settings["trajectory"],
    }
    for key in ("home", "lights", "yawControl"):
        if key in settings:
            spec[key] = settings[key]
    for key in ("amslReference", "geofence"):
        if site.get(key) is not None:
            spec[key] = site[key]

    Path(args.out).write_text(json.dumps(spec))
    print(f"Show specification A written to {args.out}")

    if args.derive_b:
        spec_b = dict(spec)
        spec_b["mission"] = {**spec["mission"], "id": "bench-B"}
        spec_b["trajectory"] = _shift_trajectory(spec["trajectory"], args.shift_z)
        Path(args.derive_b).write_text(json.dumps(spec_b))
        print(
            f"Show specification B written to {args.derive_b} "
            f"(trajectory shifted by {args.shift_z} m)"
        )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha256_of_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fp:
        return [json.loads(line) for line in fp if line.strip()]


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], timeout: float, interval: float = 0.5
) -> float:
    """Polls an async predicate until it returns ``True``.

    Returns:
        the number of seconds it took

    Raises:
        trio.TooSlowError: if the predicate did not become true in time
    """
    start = monotonic()
    with trio.fail_after(timeout):
        while True:
            try:
                if await predicate():
                    return monotonic() - start
            except CommandError:
                pass
            await trio.sleep(interval)


class Bench:
    """State and logic of a single bench test run."""

    def __init__(self, client: FlockwaveClient, args: Namespace):
        self.client = client
        self.args = args
        self.uav_id: str = args.uav_id
        self.specs = {
            "A": json.loads(Path(args.show_a).read_text()),
            "B": json.loads(Path(args.show_b).read_text()),
        }
        self.spec_sha = {
            "A": _sha256_of_file(Path(args.show_a)),
            "B": _sha256_of_file(Path(args.show_b)),
        }
        self.crc_by_show: dict[str, str] = {}

    def log(self, message: str) -> None:
        """Prints a progress message in verbose mode."""
        if self.args.verbose:
            print(f"  [{_now()}] {message}", flush=True)

    def resume_from(self, records: list[dict[str, Any]]) -> int:
        """Restores the learned CRCs from earlier records.

        Returns:
            the index of the next cycle
        """
        if not records:
            return 0

        if records[-1].get("spec_sha") != self.spec_sha:
            raise BenchAbort(
                "The show specifications differ from the ones in the results "
                "file; use a new results file"
            )

        for record in records:
            if record["result"] == "OK":
                self.crc_by_show[record["show"]] = record["crc_before"]

        return records[-1]["cycle"] + 1

    async def get_show_crc(self) -> str:
        result = await self.client.run_uav_command(
            self.uav_id, "__file_crc", timeout=60, path=SHOW_FILE_PATH
        )
        return str(result)

    async def is_sleeping(self) -> bool:
        return SLEEPING_ERROR_CODE in await self.client.get_uav_errors(self.uav_id)

    async def is_awake(self) -> bool:
        return not await self.is_sleeping()

    async def upload(self, show: str) -> None:
        await self.client.run_uav_command(
            self.uav_id,
            "__show_upload",
            timeout=self.args.upload_timeout,
            show=self.specs[show],
        )

    async def start_and_cancel_upload(self, show: str) -> None:
        self.log(f"Starting upload of show {show} (to be cancelled)")
        receipt, _ = await self.client.start_uav_request(
            "OBJ-CMD",
            self.uav_id,
            command="__show_upload",
            args=[],
            kwds={"show": self.specs[show]},
        )
        if receipt is None:
            # The command finished immediately; nothing to cancel
            self.log("Upload finished immediately; nothing to cancel")
            return

        delay = uniform(0.5, 3)
        await trio.sleep(delay)
        self.log(f"Cancelling upload after {delay:.1f}s")
        await self.client.cancel(receipt)

        # Give the server time to clean up the MAVFTP session on the drone
        outcome = "no response from server in 5s"
        with trio.move_on_after(5):
            try:
                await self.client.wait_for_receipt(receipt, timeout=5)
                outcome = "upload had already finished"
            except CommandError as ex:
                outcome = str(ex)
        self.log(f"Cancelled upload ended: {outcome}")

    async def run_cycle(self, cycle: int) -> dict[str, Any]:
        args = self.args
        show = "A" if cycle % 2 == 0 else "B"
        other = "B" if show == "A" else "A"

        # Each delay is used for an A and a B cycle in a row; a plain
        # cycle % len(delays) would always pair the same show with the same
        # delay when there are two delays
        delay = args.delay[(cycle // 2) % len(args.delay)]

        record: dict[str, Any] = {
            "cycle": cycle,
            "started_at": _now(),
            "uav_id": self.uav_id,
            "show": show,
            "delay": delay,
            "sleep": args.sleep,
            "cancels": args.cancels,
            "tag": args.tag,
            "spec_sha": self.spec_sha,
        }

        def finish(result: str, note: str | None = None) -> dict[str, Any]:
            record["result"] = result
            if note:
                record["note"] = note
            record["finished_at"] = _now()
            return record

        # Cancelled upload attempts, like the operator did on 2026-11-07
        for _ in range(args.cancels):
            try:
                await self.start_and_cancel_upload(show)
            except CommandError as ex:
                self.log(f"Cancelled upload attempt failed: {ex}")
                record.setdefault("cancel_errors", []).append(str(ex))

        # Full upload
        self.log(f"Uploading show {show}")
        start = monotonic()
        try:
            await self.upload(show)
        except CommandError as ex:
            record["upload_s"] = round(monotonic() - start, 3)
            self.log(f"Upload failed after {record['upload_s']}s: {ex}")
            return finish("INFRA", f"upload failed: {ex}")
        record["upload_s"] = round(monotonic() - start, 3)
        self.log(f"Upload finished in {record['upload_s']}s")

        # Reference CRC before sleep
        self.log("Reading CRC of show file before sleep")
        try:
            crc_before = await self.get_show_crc()
        except CommandError as ex:
            self.log(f"CRC query failed: {ex}")
            return finish("INFRA", f"CRC query before sleep failed: {ex}")
        record["crc_before"] = crc_before
        self.log(f"CRC before sleep: {crc_before}")

        if crc_before == self.crc_by_show.get(other):
            return finish("UPLOAD_MISMATCH", "file is the other show before sleep")

        expected = self.crc_by_show.get(show)
        if expected is None:
            if crc_before in self.crc_by_show.values():
                raise BenchAbort("Shows A and B have the same CRC; use other shows")
            self.crc_by_show[show] = crc_before
        elif crc_before != expected:
            return finish(
                "UPLOAD_MISMATCH",
                f"CRC before sleep is {crc_before}, expected {expected}",
            )

        # Sleep
        self.log(f"Waiting {delay}s before sending the drone to sleep")
        await trio.sleep(delay)
        self.log("Sending UAV-SLEEP")
        record["sleep_sent_at"] = _now()
        try:
            record["sleep_response"] = await self.client.run_uav_request(
                "UAV-SLEEP", self.uav_id, timeout=30
            )
        except CommandError as ex:
            # The ESP32 handles the command, so an error here is worth
            # recording but not necessarily fatal
            record["sleep_response"] = f"error: {ex}"
            self.log(f"UAV-SLEEP returned an error: {ex}")

        self.log("Waiting for the drone to report sleeping")
        try:
            record["sleep_detected_s"] = round(
                await _wait_until(self.is_sleeping, args.sleep_timeout), 3
            )
        except trio.TooSlowError:
            self.log(f"Drone not sleeping after {args.sleep_timeout}s")
            return finish("INFRA", "drone did not report sleeping in time")
        self.log(f"Drone is sleeping after {record['sleep_detected_s']}s")

        self.log(f"Keeping the drone asleep for {args.sleep}s")
        await trio.sleep(args.sleep)

        # Wake-up
        self.log("Sending UAV-WAKEUP")
        try:
            record["wakeup_response"] = await self.client.run_uav_request(
                "UAV-WAKEUP", self.uav_id, timeout=30
            )
        except CommandError as ex:
            record["wakeup_response"] = f"error: {ex}"
            self.log(f"UAV-WAKEUP returned an error: {ex}")

        self.log("Waiting for the drone to wake up")
        try:
            record["wakeup_detected_s"] = round(
                await _wait_until(self.is_awake, args.wake_timeout), 3
            )
        except trio.TooSlowError:
            self.log(f"Drone still sleeping after {args.wake_timeout}s")
            return finish("INFRA", "drone did not wake up in time")
        self.log(f"Drone is awake after {record['wakeup_detected_s']}s")

        # CRC after wake-up. The flight controller may need a few more seconds
        # after its first heartbeat before MAVFTP works.
        crc_after: str | None = None
        last_error: str | None = None
        self.log("Reading CRC of show file after wake-up")
        with trio.move_on_after(args.wake_timeout):
            while crc_after is None:
                try:
                    crc_after = await self.get_show_crc()
                except CommandError as ex:
                    last_error = str(ex)
                    if "not found" in last_error.lower():
                        self.log("Show file not found")
                        break
                    self.log(f"CRC query failed, retrying in 2s: {ex}")
                    await trio.sleep(2)

        if crc_after is None:
            if last_error and "not found" in last_error.lower():
                return finish("CORRUPT", "show file missing after wake-up")
            return finish("INFRA", f"CRC query after wake-up failed: {last_error}")

        record["crc_after"] = crc_after
        self.log(f"CRC after wake-up: {crc_after}")
        if crc_after == crc_before:
            return finish("OK")
        elif crc_after == self.crc_by_show.get(other):
            return finish("ROLLBACK")
        else:
            return finish("CORRUPT", "CRC after wake-up matches neither show")


async def run(args: Namespace) -> int:
    """Runs the bench test.

    Returns:
        the exit code of the script
    """
    out = Path(args.out)
    records = _read_results(out)

    async with FlockwaveClient.connect(args.host, args.port) as client:
        # Errors must be handled here, before they reach the nursery of the
        # client and get wrapped in an exception group
        try:
            return await _run_cycles(client, args, out, records)
        except BenchAbort as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 1


async def _run_cycles(
    client: FlockwaveClient, args: Namespace, out: Path, records: list[dict[str, Any]]
) -> int:
    bench = Bench(client, args)
    first_cycle = bench.resume_from(records)
    if first_cycle:
        print(f"Resuming from cycle {first_cycle}")

    body = await client.request({"type": "UAV-LIST"})
    if args.uav_id not in (body.get("ids") or ()):
        raise BenchAbort(f"UAV {args.uav_id} is not known to the server")

    consecutive_infra = 0
    for cycle in range(first_cycle, args.cycles):
        if args.verbose:
            print(f"Cycle {cycle}:")
        record = await bench.run_cycle(cycle)
        with out.open("a") as fp:
            fp.write(json.dumps(record) + "\n")

        result = record["result"]
        note = record.get("note", "")
        print(
            f"[{record['finished_at']}] cycle {cycle} show {record['show']} "
            f"delay {record['delay']}s: {result} {note}".rstrip()
        )

        if result in FAILURES and not args.keep_going:
            print()
            print(f"Stopped at {result}. Do not use the drone any further.")
            print("Power it off and image its SD card with dd right away.")
            return 2

        consecutive_infra = consecutive_infra + 1 if result == "INFRA" else 0
        if consecutive_infra >= args.max_infra:
            print(f"Stopped after {consecutive_infra} INFRA cycles in a row.")
            return 3

    return 0


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def summary(args: Namespace) -> None:
    records = _read_results(Path(args.results))
    groups: defaultdict[tuple, Counter] = defaultdict(Counter)
    for record in records:
        key = (record["tag"], record["delay"], record["sleep"], record["cancels"])
        groups[key][record["result"]] += 1

    print(
        f"{'tag':<16} {'delay':>6} {'sleep':>6} {'cancels':>7} {'n':>5} "
        f"{'OK':>5} {'fail':>5} {'infra':>5}  95% upper bound"
    )
    for (tag, delay, sleep, cancels), counts in sorted(groups.items()):
        failures = sum(counts[name] for name in FAILURES)
        n = counts["OK"] + failures
        if n == 0:
            bound = "-"
        elif failures == 0:
            # Exact one-sided bound for zero failures in n trials
            bound = f"{1 - 0.05 ** (1 / n):.2%}"
        else:
            bound = f"observed {failures / n:.2%}"
        print(
            f"{tag or '':<16} {delay:>6} {sleep:>6} {cancels:>7} {n:>5} "
            f"{counts['OK']:>5} {failures:>5} {counts['INFRA']:>5}  {bound}"
        )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _float_list(value: str) -> list[float]:
    return [float(item) for item in value.split(",") if item.strip()]


def main() -> None:
    parser = ArgumentParser(description=__doc__.partition("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("make-spec", help="build show specifications")
    p.add_argument("--skyc", required=True, help="the .skyc file to use")
    p.add_argument(
        "--drone", required=True, help="drone name (e.g. 'Drone 10') or 1-based index"
    )
    p.add_argument("--site", required=True, help="site file with coordinate system")
    p.add_argument("--out", required=True, help="output file for show A")
    p.add_argument("--derive-b", help="output file for show B, derived from A")
    p.add_argument(
        "--shift-z",
        type=float,
        default=1.0,
        help="vertical shift of the trajectory of show B, in meters",
    )

    p = subparsers.add_parser("run", help="run the bench test")
    p.add_argument("--host", default="localhost", help="server host")
    p.add_argument("--port", type=int, default=5001, help="server TCP port")
    p.add_argument("--uav-id", required=True, help="ID of the UAV to test")
    p.add_argument("--show-a", required=True, help="show specification A")
    p.add_argument("--show-b", required=True, help="show specification B")
    p.add_argument("--cycles", type=int, default=500, help="total number of cycles")
    p.add_argument(
        "--delay",
        type=_float_list,
        default=[10.0],
        help="seconds between upload and sleep; comma-separated list to vary it",
    )
    p.add_argument("--sleep", type=float, default=30, help="seconds of sleep")
    p.add_argument(
        "--cancels",
        type=int,
        default=0,
        help="cancelled upload attempts before the full upload",
    )
    p.add_argument("--tag", default="", help="free-form label, e.g. 'loss=15%%'")
    p.add_argument("--upload-timeout", type=float, default=120)
    p.add_argument("--sleep-timeout", type=float, default=30)
    p.add_argument("--wake-timeout", type=float, default=120)
    p.add_argument(
        "--max-infra",
        type=int,
        default=5,
        help="stop after this many INFRA cycles in a row",
    )
    p.add_argument(
        "--keep-going",
        action="store_true",
        help="do not stop at ROLLBACK, CORRUPT or UPLOAD_MISMATCH",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print each step of a cycle while it happens",
    )
    p.add_argument("--out", required=True, help="results file (JSON lines)")

    p = subparsers.add_parser("summary", help="summarize a results file")
    p.add_argument("results", help="results file (JSON lines)")

    args = parser.parse_args()
    if args.command == "make-spec":
        make_spec(args)
    elif args.command == "run":
        sys.exit(trio.run(run, args))
    else:
        summary(args)


if __name__ == "__main__":
    main()
