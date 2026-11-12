#!/usr/bin/env python3
"""Bench test for show uploads followed by sleep mode.

The script reproduces the protocol used before a show: upload the show, put
the drone to sleep, wake it up later. After each wake-up it asks the drone for
the CRC32 of its show file to detect whether the file survived the power cut
of the flight controller.

Before each upload, the script also picks a random show origin, orientation
and AMSL reference near the ones in the show specification. The server sends
them to the drone along with the show, and the drone stores them in the
``SHOW_ORIGIN_LAT``, ``SHOW_ORIGIN_LNG``, ``SHOW_ORIGIN_AMSL`` and
``SHOW_ORIENTATION`` parameters. The script reads these parameters before the
upload, after the upload and after the wake-up, and reports parameter problems
separately from show file problems.

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
                "type": "nwu"
            },
            "amslReference": null,
            "geofence": null
        }

    Note that ``origin`` is in longitude-latitude order, and the server
    accepts ``nwu`` coordinate systems only. ``amslReference`` is the centre
    of the random AMSL values; 200 m is used if it is ``null``.

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

Parameter outcomes of a cycle:

- ``PARAM_OK``: the parameters after wake-up have the values that were set.
- ``PARAM_LOST``: the parameters had the new values before sleep, but at least
  one of them differs after wake-up. The results file tells for each such
  parameter whether it reverted to its value before the upload.
- ``PARAM_NOT_APPLIED``: the parameters do not have the new values even
  before sleep.
- ``PARAM_INFRA``: the parameters could not be read.
- ``SKIPPED``: the cycle ended before the parameters could be checked after
  wake-up, for a reason unrelated to the parameters.

The run stops at the first ``ROLLBACK``, ``CORRUPT`` or ``UPLOAD_MISMATCH``
and leaves the drone untouched. Power the drone off and image its SD card
with ``dd`` before doing anything else with it. Parameter problems do not stop
the run unless ``--stop-on-param-failure`` is given.

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
import struct
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

POWER_COMMAND_ATTEMPTS = 5
"""Number of times to send ``UAV-SLEEP`` or ``UAV-WAKEUP`` before giving up."""

FAILURES = ("ROLLBACK", "CORRUPT", "UPLOAD_MISMATCH")
"""Outcomes that stop the run unless ``--keep-going`` is given."""

PARAM_LAT = "SHOW_ORIGIN_LAT"
PARAM_LNG = "SHOW_ORIGIN_LNG"
PARAM_AMSL = "SHOW_ORIGIN_AMSL"
PARAM_ORIENTATION = "SHOW_ORIENTATION"
PARAM_NAMES = (PARAM_LAT, PARAM_LNG, PARAM_AMSL, PARAM_ORIENTATION)
"""Parameters that store the show coordinate system on the drone."""

PARAM_FAILURES = ("PARAM_LOST", "PARAM_NOT_APPLIED")
"""Parameter outcomes that count as failures."""

PARAM_READ_ATTEMPTS = 5
"""Number of times to read the parameters before giving up."""

DEFAULT_AMSL = 200.0
"""Centre of the random AMSL values if the show specification has none."""


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
    if coordinate_system["type"] != "nwu":
        raise SystemExit("The server supports nwu coordinate systems only")

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


def _as_float32(value: float) -> float:
    """Rounds a number to the nearest float32 value.

    ArduPilot sends every parameter value as a float32, even integer ones. The
    latitude and longitude parameters do not fit into the 24-bit mantissa, so
    the values read back must be compared with the expected values rounded
    the same way.
    """
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _randomize_coordinate_system(
    spec: dict[str, Any],
    origin_jitter: float,
    amsl_jitter: float,
    avoid: dict[str, float] | None,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Returns a copy of the show specification with a random origin,
    orientation and AMSL reference near the ones in the specification.

    Args:
        spec: the show specification
        origin_jitter: maximum change of the latitude and longitude, in degrees
        amsl_jitter: maximum change of the AMSL reference, in meters
        avoid: parameter values that none of the new parameter values may
            match, typically the current values on the drone

    Returns:
        the new show specification, and the parameter values that the drone
        should report after the upload
    """
    coordinate_system = spec["coordinateSystem"]
    base_lon, base_lat = coordinate_system["origin"]
    base_amsl = spec.get("amslReference")
    if base_amsl is None:
        base_amsl = DEFAULT_AMSL

    while True:
        lat = round(base_lat + uniform(-origin_jitter, origin_jitter), 7)
        lon = round(base_lon + uniform(-origin_jitter, origin_jitter), 7)
        orientation = round(uniform(0, 360), 1)
        amsl = round(base_amsl + uniform(-amsl_jitter, amsl_jitter), 1)

        # Same arithmetic as MAVLinkUAV.upload_show() in the server
        expected = {
            PARAM_LAT: _as_float32(int(lat * 1e7)),
            PARAM_LNG: _as_float32(int(lon * 1e7)),
            PARAM_AMSL: _as_float32(int(amsl * 1e3)),
            PARAM_ORIENTATION: _as_float32(orientation % 360),
        }

        # Every parameter must change; otherwise a lost change could not be
        # told apart from a successful one
        if avoid is None or all(expected[n] != avoid.get(n) for n in PARAM_NAMES):
            break

    result = dict(spec)
    result["coordinateSystem"] = {
        **coordinate_system,
        "origin": [lon, lat],
        "orientation": orientation,
    }
    result["amslReference"] = amsl
    return result, expected


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

    async def read_params(self) -> dict[str, float]:
        """Reads the show coordinate system parameters from the drone."""
        result = {}
        for name in PARAM_NAMES:
            value = await self.client.run_uav_request(
                "PRM-GET", self.uav_id, timeout=30, name=name
            )
            result[name] = float(value)
        return result

    async def check_params(
        self, expected: dict[str, float] | None
    ) -> tuple[dict[str, float] | None, str | None]:
        """Reads the show coordinate system parameters, retrying if the read
        fails or if the values differ from the expected ones.

        Args:
            expected: the expected parameter values; ``None`` to accept any
                values

        Returns:
            the values read in the last attempt, or ``None`` if all attempts
            failed; and the error of the last failed read
        """
        values: dict[str, float] | None = None
        error: str | None = None

        for attempt in range(1, PARAM_READ_ATTEMPTS + 1):
            try:
                values = await self.read_params()
            except CommandError as ex:
                error = str(ex)
                self.log(
                    f"Parameter read failed (attempt {attempt}/"
                    f"{PARAM_READ_ATTEMPTS}): {ex}"
                )
            else:
                error = None
                if expected is None or values == expected:
                    return values, None
                wrong = ", ".join(n for n in PARAM_NAMES if values[n] != expected[n])
                self.log(
                    f"Parameters differ (attempt {attempt}/"
                    f"{PARAM_READ_ATTEMPTS}): {wrong}"
                )

            if attempt < PARAM_READ_ATTEMPTS:
                await trio.sleep(2)

        return values, error

    async def is_sleeping(self) -> bool:
        return SLEEPING_ERROR_CODE in await self.client.get_uav_errors(self.uav_id)

    async def is_awake(self) -> bool:
        return not await self.is_sleeping()

    async def change_power_state(
        self, request: str, record: dict[str, Any], key: str
    ) -> bool:
        """Sends the drone to sleep or wakes it up, and waits until the server
        reports the new state.

        The request is sent again if the drone does not reach the new state in
        time, up to `POWER_COMMAND_ATTEMPTS` attempts in total.

        Args:
            request: ``UAV-SLEEP`` or ``UAV-WAKEUP``
            record: the record of the cycle. The method stores the response
                of the last attempt, the number of attempts and the time it
                took to reach the new state, under keys starting with `key`.
            key: prefix of the keys in the record (``sleep`` or ``wakeup``)

        Returns:
            whether the drone reached the new state
        """
        if request == "UAV-SLEEP":
            predicate, timeout, state = (
                self.is_sleeping,
                self.args.sleep_timeout,
                "sleeping",
            )
        else:
            predicate, timeout, state = self.is_awake, self.args.wake_timeout, "awake"

        start = monotonic()
        record[f"{key}_sent_at"] = _now()

        for attempt in range(1, POWER_COMMAND_ATTEMPTS + 1):
            record[f"{key}_attempts"] = attempt
            self.log(f"Sending {request} (attempt {attempt}/{POWER_COMMAND_ATTEMPTS})")
            try:
                record[f"{key}_response"] = await self.client.run_uav_request(
                    request, self.uav_id, timeout=30
                )
            except CommandError as ex:
                # The ESP32 handles the command, so an error here is worth
                # recording but not necessarily fatal
                record[f"{key}_response"] = f"error: {ex}"
                self.log(f"{request} returned an error: {ex}")

            self.log(f"Waiting for the drone to report {state}")
            try:
                await _wait_until(predicate, timeout)
            except trio.TooSlowError:
                self.log(f"Drone not {state} after {timeout}s")
                continue

            record[f"{key}_detected_s"] = round(monotonic() - start, 3)
            self.log(f"Drone is {state} after {record[f'{key}_detected_s']}s")
            return True

        return False

    async def upload(self, spec: dict[str, Any]) -> None:
        await self.client.run_uav_command(
            self.uav_id,
            "__show_upload",
            timeout=self.args.upload_timeout,
            show=spec,
        )

    async def start_and_cancel_upload(self, show: str, spec: dict[str, Any]) -> None:
        self.log(f"Starting upload of show {show} (to be cancelled)")
        receipt, _ = await self.client.start_uav_request(
            "OBJ-CMD",
            self.uav_id,
            command="__show_upload",
            args=[],
            kwds={"show": spec},
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

    def check_params_after_wakeup(
        self,
        record: dict[str, Any],
        params_after: dict[str, float] | None,
        error: str | None,
    ) -> None:
        """Stores the parameter outcome of a cycle in its record."""
        if params_after is None:
            record["param_result"] = "PARAM_INFRA"
            record["param_note"] = f"read after wake-up failed: {error}"
            self.log(f"Could not read parameters after wake-up: {error}")
            return

        record["params_after"] = params_after
        expected = record["params_expected"]
        start = record.get("params_start") or {}

        lost = {}
        for name in PARAM_NAMES:
            if params_after[name] != expected[name]:
                lost[name] = (
                    "reverted" if params_after[name] == start.get(name) else "other"
                )

        if lost:
            record["param_result"] = "PARAM_LOST"
            record["params_lost"] = lost
            record["param_note"] = ", ".join(
                f"{name} {how}" for name, how in lost.items()
            )
            self.log(f"Parameters lost after wake-up: {record['param_note']}")
        else:
            record["param_result"] = "PARAM_OK"
            self.log("Parameters after wake-up are correct")

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
            "param_result": "SKIPPED",
        }

        def finish(result: str, note: str | None = None) -> dict[str, Any]:
            record["result"] = result
            if note:
                record["note"] = note
            record["finished_at"] = _now()
            return record

        # A drone left asleep by an earlier cycle cannot accept uploads
        try:
            sleeping_at_start = await self.is_sleeping()
        except CommandError as ex:
            return finish("INFRA", f"cannot query drone status: {ex}")
        if sleeping_at_start:
            self.log("Drone is still sleeping from an earlier cycle; waking it up")
            if not await self.change_power_state(
                "UAV-WAKEUP", record, "initial_wakeup"
            ):
                return finish("INFRA", "drone left asleep earlier did not wake up")

        # Parameters before the upload, to tell whether a lost value reverted
        # to its earlier value
        self.log("Reading parameters before upload")
        params_start, error = await self.check_params(None)
        if params_start is None:
            self.log(f"Could not read parameters before upload: {error}")
        else:
            record["params_start"] = params_start

        spec, params_expected = _randomize_coordinate_system(
            self.specs[show], args.origin_jitter, args.amsl_jitter, params_start
        )
        record["params_expected"] = params_expected
        cs = spec["coordinateSystem"]
        self.log(
            f"Show origin {cs['origin'][1]:.7f}, {cs['origin'][0]:.7f}, "
            f"orientation {cs['orientation']}, AMSL {spec['amslReference']} m"
        )

        # Cancelled upload attempts, like the operator did on 2026-11-07
        for _ in range(args.cancels):
            try:
                await self.start_and_cancel_upload(show, spec)
            except CommandError as ex:
                self.log(f"Cancelled upload attempt failed: {ex}")
                record.setdefault("cancel_errors", []).append(str(ex))

        # Full upload
        self.log(f"Uploading show {show}")
        start = monotonic()
        try:
            await self.upload(spec)
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

        # Parameters before sleep
        self.log("Reading parameters before sleep")
        params_before, error = await self.check_params(params_expected)
        if params_before is None:
            record["param_result"] = "PARAM_INFRA"
            record["param_note"] = f"read before sleep failed: {error}"
        else:
            record["params_before"] = params_before
            if params_before == params_expected:
                self.log("Parameters before sleep are correct")
            else:
                record["param_result"] = "PARAM_NOT_APPLIED"
                record["param_note"] = "wrong before sleep: " + ", ".join(
                    n for n in PARAM_NAMES if params_before[n] != params_expected[n]
                )

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
        if not await self.change_power_state("UAV-SLEEP", record, "sleep"):
            return finish("INFRA", "drone did not go to sleep")

        self.log(f"Keeping the drone asleep for {args.sleep}s")
        await trio.sleep(args.sleep)

        # Wake-up
        if not await self.change_power_state("UAV-WAKEUP", record, "wakeup"):
            return finish("INFRA", "drone did not wake up")

        # Parameters after wake-up, checked only if they were right before sleep
        if params_before == params_expected:
            self.log("Reading parameters after wake-up")
            self.check_params_after_wakeup(
                record, *await self.check_params(params_expected)
            )

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
        param_result = record["param_result"]
        param_note = record.get("param_note", "")
        print(
            f"[{record['finished_at']}] cycle {cycle} show {record['show']} "
            f"delay {record['delay']}s: file {result} {note}".rstrip()
            + f" | params {param_result} {param_note}".rstrip()
        )

        if result in FAILURES and not args.keep_going:
            print()
            print(f"Stopped at {result}. Do not use the drone any further.")
            print("Power it off and image its SD card with dd right away.")
            return 2

        if param_result in PARAM_FAILURES and args.stop_on_param_failure:
            print()
            print(f"Stopped at {param_result}.")
            return 4

        consecutive_infra = consecutive_infra + 1 if result == "INFRA" else 0
        if consecutive_infra >= args.max_infra:
            print(f"Stopped after {consecutive_infra} INFRA cycles in a row.")
            return 3

    return 0


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def _print_outcome_table(
    title: str,
    records: list[dict[str, Any]],
    field: str,
    ok: str,
    failures: tuple[str, ...],
) -> None:
    """Prints the counts of one kind of outcome per test setting."""
    groups: defaultdict[tuple, Counter] = defaultdict(Counter)
    for record in records:
        if field not in record:
            # Records written before this outcome was introduced
            continue
        key = (record["tag"], record["delay"], record["sleep"], record["cancels"])
        groups[key][record[field]] += 1

    print(title)
    print(
        f"{'tag':<16} {'delay':>6} {'sleep':>6} {'cancels':>7} {'n':>5} "
        f"{'OK':>5} {'fail':>5} {'other':>5}  95% upper bound"
    )
    for (tag, delay, sleep, cancels), counts in sorted(groups.items()):
        failed = sum(counts[name] for name in failures)
        n = counts[ok] + failed
        other = sum(counts.values()) - n
        if n == 0:
            bound = "-"
        elif failed == 0:
            # Exact one-sided bound for zero failures in n trials
            bound = f"{1 - 0.05 ** (1 / n):.2%}"
        else:
            bound = f"observed {failed / n:.2%}"
        print(
            f"{tag or '':<16} {delay:>6} {sleep:>6} {cancels:>7} {n:>5} "
            f"{counts[ok]:>5} {failed:>5} {other:>5}  {bound}"
        )


def summary(args: Namespace) -> None:
    records = _read_results(Path(args.results))
    _print_outcome_table("Show file", records, "result", "OK", FAILURES)
    print()
    _print_outcome_table(
        "Parameters", records, "param_result", "PARAM_OK", PARAM_FAILURES
    )
    print()
    print(
        "'other' counts cycles without a conclusion: INFRA for the show file, "
        "PARAM_INFRA and SKIPPED for the parameters."
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
    p.add_argument("--sleep", type=float, default=10, help="seconds of sleep")
    p.add_argument(
        "--cancels",
        type=int,
        default=0,
        help="cancelled upload attempts before the full upload",
    )
    p.add_argument("--tag", default="", help="free-form label, e.g. 'loss=15%%'")
    p.add_argument("--upload-timeout", type=float, default=120)
    p.add_argument(
        "--sleep-timeout",
        type=float,
        default=10,
        help="seconds to wait for sleep after each UAV-SLEEP attempt",
    )
    p.add_argument(
        "--wake-timeout",
        type=float,
        default=45,
        help=(
            "seconds to wait for wake-up after each UAV-WAKEUP attempt, and "
            "for the CRC query after wake-up"
        ),
    )
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
        "--stop-on-param-failure",
        action="store_true",
        help="stop at PARAM_LOST or PARAM_NOT_APPLIED",
    )
    p.add_argument(
        "--origin-jitter",
        type=float,
        default=0.01,
        help="maximum random change of the show origin, in degrees",
    )
    p.add_argument(
        "--amsl-jitter",
        type=float,
        default=50,
        help="maximum random change of the AMSL reference, in meters",
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
