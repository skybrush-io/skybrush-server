"""Support for the Lighthouse positioning system of Crazyflie drones.

The Lighthouse system consists of up to 16 base stations that sweep the flight
area with infrared light. Each Crazyflie needs to know the geometry (position
and orientation) and the calibration data of each base station to determine
its own position.

The server keeps its own copy of the Lighthouse configuration once the user
provides one, either by uploading a configuration file or by importing it from
a drone. The server writes this configuration to every connected drone whose
configuration differs from it, and keeps doing so as new drones connect.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypedDict, cast

from aiocflib.crazyflie.lighthouse import (
    LighthouseBsCalibration,
    LighthouseBsGeometry,
    LighthouseConfiguration,
    LighthouseSystemType,
)
from flockwave.gps.vectors import PositionXYZ
from flockwave.spec.errors import FlockwaveErrorCode
from trio import move_on_after, sleep
from trio_util import AsyncValue

from flockwave.server.errors import NotSupportedError
from flockwave.server.ext.lps.model import (
    Anchor,
    LocalPositioningSystem,
    LocalPositioningSystemType,
)
from flockwave.server.model.commands import Progress, ProgressEventsWithSuspension

from .math import quaternion_from_rotation_matrix

if TYPE_CHECKING:
    from aiocflib.crazyflie import Crazyflie

__all__ = (
    "LIGHTHOUSE_LPS_ID",
    "LIGHTHOUSE_LPS_TYPE_ID",
    "LighthouseConfigurationJSON",
    "LighthouseLocalPositioningSystem",
    "LighthouseLocalPositioningSystemType",
    "LighthouseUAV",
    "read_lighthouse_configuration",
    "write_lighthouse_configuration",
)


LIGHTHOUSE_LPS_ID = "lighthouse"
"""ID of the Lighthouse local positioning system that the Crazyflie extension
registers.
"""

LIGHTHOUSE_LPS_TYPE_ID = "crazyflie-lighthouse"
"""ID of the type of the Lighthouse local positioning system."""

MAX_BASE_STATIONS = 16
"""Maximum number of base stations that the Crazyflie firmware supports."""

IDLE_POLL_INTERVAL = 5.0
"""Number of seconds to wait between consecutive checks of whether a drone is
idle and its Lighthouse configuration can be updated. The check uses the
status information that the server already has; it does not communicate with
the drone.
"""

RETRY_INTERVAL = 30.0
"""Number of seconds to wait before trying to synchronize the Lighthouse
configuration of a drone again after a failed attempt.
"""


class LighthouseGeometryJSON(TypedDict):
    """JSON representation of the geometry of a single base station."""

    origin: list[float]
    """Position of the base station, in meters."""

    rotation: list[list[float]]
    """Rotation matrix of the base station, row by row. The matrix rotates
    vectors from the frame of the base station to the world frame.
    """


class LighthouseCalibrationSweepJSON(TypedDict):
    """JSON representation of the calibration data of a single sweep plane of
    a base station.
    """

    phase: float
    tilt: float
    curve: float
    gibmag: float
    gibphase: float
    ogeemag: float
    ogeephase: float


class LighthouseCalibrationJSON(TypedDict):
    """JSON representation of the calibration data of a single base station."""

    sweeps: list[LighthouseCalibrationSweepJSON]
    uid: int


class LighthouseConfigurationJSON(TypedDict):
    """JSON representation of the configuration of a Lighthouse system.

    The format is the same as the one used by the Crazyflie client to save
    and load Lighthouse configurations. Base stations are identified by their
    zero-based index in the ``geos`` and ``calibs`` mappings, i.e. the base
    station on channel 1 has index 0.
    """

    version: NotRequired[str]
    type: NotRequired[str]
    systemType: NotRequired[int]
    geos: NotRequired[dict[str, LighthouseGeometryJSON]]
    calibs: NotRequired[dict[str, LighthouseCalibrationJSON]]


class LighthouseUAV(Protocol):
    """Interface specification of the UAVs that the Lighthouse local
    positioning system can work with.
    """

    @property
    def id(self) -> str: ...

    @property
    def is_idle_on_ground(self) -> bool:
        """Whether the UAV is on the ground with its motors stopped. The
        Lighthouse configuration of the UAV is updated only in this state.
        """
        ...

    async def get_lighthouse_configuration(self) -> LighthouseConfiguration: ...

    async def set_lighthouse_configuration(
        self, config: LighthouseConfiguration
    ) -> None: ...


class LighthouseLocalPositioningSystem(
    LocalPositioningSystem[LighthouseConfigurationJSON]
):
    """Local positioning system that represents the Lighthouse base stations
    used by Crazyflie drones.
    """

    log: Logger | None
    """Logger that the LPS uses to report problems with individual drones."""

    _configuration: LighthouseConfiguration | None
    """The Lighthouse configuration that the server enforces on the drones;
    ``None`` if the user has not provided one yet.
    """

    _configuration_file: Path | None
    """File that the configuration is saved into every time it is set;
    ``None`` if the configuration is not saved.
    """

    _synced_uav_ids: set[str]
    """IDs of the UAVs that are known to have the current configuration of
    the server.
    """

    _version: AsyncValue[int]
    """Version number of the configuration. Incremented every time the
    configuration is set, even if it did not change.
    """

    _find_uav: Callable[[str], LighthouseUAV | None]
    """Function that returns the UAV with the given ID, or ``None`` if there is
    no such UAV.
    """

    def __init__(
        self,
        find_uav: Callable[[str], LighthouseUAV | None],
        log: Logger | None = None,
    ) -> None:
        """Constructor.

        Parameters:
            find_uav: function that returns the UAV with the given ID, or
                ``None`` if there is no such UAV
            log: logger that the LPS uses to report problems with individual
                drones
        """
        super().__init__()
        self.name = "Lighthouse"
        self.log = log
        self._configuration = None
        self._configuration_file = None
        self._find_uav = find_uav
        self._synced_uav_ids = set()
        self._version = AsyncValue(0)

    @property
    def configuration(self) -> LighthouseConfiguration | None:
        """The Lighthouse configuration that the server enforces on the drones;
        ``None`` if the user has not provided one yet.
        """
        return self._configuration

    @property
    def configuration_file(self) -> Path | None:
        """File that the configuration is saved into every time it is set;
        ``None`` if the configuration is not saved.
        """
        return self._configuration_file

    async def calibrate(
        self, params: Mapping[str, Any]
    ) -> ProgressEventsWithSuspension[None, Any]:
        """Establishes the geometry of the base stations.

        The only supported method is ``import``, which reads the geometry and
        calibration data of the base stations from a drone that was set up
        with another tool, e.g., the Crazyflie client. The server writes the
        imported configuration to all the other drones.

        Parameters:
            params: the calibration parameters. ``method`` must be ``import``
                and ``uav`` is the ID of the drone to import the configuration
                from.
        """
        uav = self._get_source_uav_for_import(params)

        yield Progress(message=f"Reading Lighthouse configuration from {uav.id}")
        config = await uav.get_lighthouse_configuration()
        if not config.geometries:
            raise RuntimeError(
                f"UAV {uav.id} does not know the geometry of any base station"
            )

        self._set_configuration(config)

        # No need to check the source UAV again
        self._synced_uav_ids.add(uav.id)

        yield Progress(percentage=100, message="Lighthouse configuration imported")

    async def get_configuration(self) -> LighthouseConfigurationJSON:
        return lighthouse_configuration_to_json(
            self._configuration or LighthouseConfiguration()
        )

    async def use_configuration_file(self, path: Path) -> None:
        """Loads the configuration from the given file if the file exists, and
        saves the configuration into the same file every time it is set from
        now on. Clearing the configuration deletes the file.

        An invalid file is logged and ignored. It is kept until the
        configuration is set again, giving the user a chance to fix it.

        Parameters:
            path: the file to load the configuration from and to save it into
        """
        if path.exists():
            try:
                with path.open(encoding="utf-8") as fp:
                    cfg = json.load(fp)
                await self.configure(cfg)
            except Exception as ex:
                if self.log:
                    self.log.error(
                        f"Ignoring invalid Lighthouse configuration in "
                        f"{str(path)!r}: {ex}"
                    )

        self._configuration_file = path

    async def keep_uav_in_sync(self, uav: LighthouseUAV) -> None:
        """Task that writes the Lighthouse configuration of the server to the
        given UAV if the configuration of the UAV differs from it.

        Each UAV is checked once, when its task starts for the first time. The
        server remembers the UAVs that have the current configuration and does
        not check them again when they reconnect. All UAVs are checked again
        when the configuration of the server is set, even if it did not
        change. The configuration of a UAV is updated only when the UAV is idle
        on the ground. Failed attempts are retried periodically.

        The task runs until it is cancelled. It is meant to be started when the
        connection to the UAV is established and cancelled when the connection
        is lost.
        """
        failed = False

        async for _ in self._version.eventual_values():
            while self._needs_sync(uav):
                while not uav.is_idle_on_ground:
                    await sleep(IDLE_POLL_INTERVAL)

                # The configuration may have changed while we were waiting
                config, version = self._configuration, self._version.value
                if config is None or not self._needs_sync(uav):
                    break

                try:
                    current = await uav.get_lighthouse_configuration()
                    if not are_lighthouse_configurations_equal(current, config):
                        await uav.set_lighthouse_configuration(config)
                        self._log_info("Lighthouse configuration updated", uav)
                except NotSupportedError:
                    # Drone has no Lighthouse support; nothing to synchronize
                    return
                except Exception as ex:
                    # Log only the first failure in a series as a warning to
                    # avoid flooding the log while retrying
                    message = f"Failed to update Lighthouse configuration: {ex}"
                    if failed:
                        self._log_debug(message, uav)
                    else:
                        self._log_warning(message, uav)
                    failed = True

                    with move_on_after(RETRY_INTERVAL):
                        await self._version.wait_transition()
                else:
                    failed = False

                    # Do not mark the UAV as synchronized if the configuration
                    # changed while we were writing it
                    if self._version.value == version:
                        self._synced_uav_ids.add(uav.id)

    async def _configure_inner(self, cfg: LighthouseConfigurationJSON) -> None:
        config = lighthouse_configuration_from_json(cfg)
        self._set_configuration(config if config.bs_ids else None)

    def _get_source_uav_for_import(self, params: Mapping[str, Any]) -> LighthouseUAV:
        unknown_keys = set(params.keys()) - {"method", "uav"}
        if unknown_keys:
            raise RuntimeError(
                f"Unknown calibration parameters: {', '.join(sorted(unknown_keys))}"
            )

        method = params.get("method")
        if method != "import":
            raise RuntimeError(f"Unsupported calibration method: {method!r}")

        uav_id = params.get("uav")
        if not isinstance(uav_id, str):
            raise RuntimeError(
                "Calibration parameters must contain the ID of the UAV to "
                "import the configuration from"
            )

        uav = self._find_uav(uav_id)
        if uav is None:
            raise RuntimeError(f"No such Crazyflie UAV: {uav_id}")

        return uav

    def _log_debug(self, message: str, uav: LighthouseUAV) -> None:
        if self.log:
            self.log.debug(message, extra={"id": uav.id})

    def _log_info(self, message: str, uav: LighthouseUAV) -> None:
        if self.log:
            self.log.info(message, extra={"id": uav.id})

    def _log_warning(self, message: str, uav: LighthouseUAV) -> None:
        if self.log:
            self.log.warning(message, extra={"id": uav.id})

    def _needs_sync(self, uav: LighthouseUAV) -> bool:
        """Returns whether the configuration of the given UAV needs to be
        checked against the configuration of the server.
        """
        return self._configuration is not None and uav.id not in self._synced_uav_ids

    def _set_configuration(self, config: LighthouseConfiguration | None) -> None:
        self._configuration = config
        self._synced_uav_ids.clear()
        self._version.value += 1
        self._update_anchors()
        self.notify_updated()
        self._save_configuration()

    def _save_configuration(self) -> None:
        """Saves the current configuration into the configuration file, or
        deletes the file if there is no configuration.

        Errors are logged. The configuration remains in effect even if it
        could not be saved.
        """
        path = self._configuration_file
        if path is None:
            return

        try:
            if self._configuration is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as fp:
                    json.dump(
                        lighthouse_configuration_to_json(self._configuration),
                        fp,
                        indent=2,
                    )
        except Exception as ex:
            if self.log:
                self.log.error(
                    f"Error while saving Lighthouse configuration to "
                    f"{str(path)!r}: {ex}"
                )

    def _update_anchors(self) -> None:
        """Updates the list of anchors from the current configuration."""
        config = self._configuration
        self.anchors = (
            [
                _create_anchor(index, config.geometries.get(index))
                for index in sorted(config.bs_ids)
            ]
            if config
            else []
        )


class LighthouseLocalPositioningSystemType(
    LocalPositioningSystemType[LighthouseLocalPositioningSystem]
):
    """Type of the Lighthouse local positioning system of Crazyflie drones."""

    _find_uav: Callable[[str], LighthouseUAV | None]
    _log: Logger | None

    def __init__(
        self,
        find_uav: Callable[[str], LighthouseUAV | None],
        log: Logger | None = None,
    ) -> None:
        """Constructor.

        Parameters:
            find_uav: function that returns the UAV with the given ID, or
                ``None`` if there is no such UAV
            log: logger that the created instances use to report problems with
                individual drones
        """
        self._find_uav = find_uav
        self._log = log

    @property
    def description(self) -> str:
        return "Lighthouse positioning system of Crazyflie drones"

    @property
    def name(self) -> str:
        return "Lighthouse"

    def create(self) -> LighthouseLocalPositioningSystem:
        return LighthouseLocalPositioningSystem(self._find_uav, self._log)

    def get_configuration_schema(self) -> dict[str, Any]:
        return CONFIGURATION_SCHEMA


def are_lighthouse_configurations_equal(
    first: LighthouseConfiguration, second: LighthouseConfiguration
) -> bool:
    """Returns whether two Lighthouse configurations are equal in the form
    they are stored on a Crazyflie.

    The data are compared after converting them to the single-precision
    representation used by the Crazyflie. This makes a configuration read
    back from a drone equal to the one written to it. The system type is
    compared only if both configurations specify it.
    """
    types = {first.system_type, second.system_type}
    if len(types - {LighthouseSystemType.UNKNOWN}) > 1:
        return False

    return _to_bytes(first.geometries) == _to_bytes(second.geometries) and _to_bytes(
        first.calibrations
    ) == _to_bytes(second.calibrations)


def lighthouse_configuration_from_json(
    obj: LighthouseConfigurationJSON,
) -> LighthouseConfiguration:
    """Converts the JSON representation of a Lighthouse configuration into a
    LighthouseConfiguration_ object.

    The JSON representation is assumed to match the configuration schema of
    the Lighthouse LPS type.
    """
    system_type = obj.get("systemType")
    return LighthouseConfiguration(
        system_type=(
            LighthouseSystemType(system_type)
            if system_type is not None
            else LighthouseSystemType.UNKNOWN
        ),
        calibrations={
            int(key): LighthouseBsCalibration.from_json(dict(value))
            for key, value in obj.get("calibs", {}).items()
        },
        geometries={
            int(key): LighthouseBsGeometry.from_json(dict(value))
            for key, value in obj.get("geos", {}).items()
        },
    )


def lighthouse_configuration_to_json(
    config: LighthouseConfiguration,
) -> LighthouseConfigurationJSON:
    """Converts a LighthouseConfiguration_ object into its JSON representation
    that the Crazyflie client can also load.
    """
    result: LighthouseConfigurationJSON = {
        "version": "1",
        "type": "lighthouse_system_configuration",
        "geos": {
            str(index): {
                "origin": list(geometry.origin),
                "rotation": [list(row) for row in geometry.rotation_matrix],
            }
            for index, geometry in sorted(config.geometries.items())
        },
        "calibs": {
            str(index): {
                "sweeps": [
                    cast(LighthouseCalibrationSweepJSON, sweep.to_json())
                    for sweep in calibration.sweeps
                ],
                "uid": calibration.uid,
            }
            for index, calibration in sorted(config.calibrations.items())
        },
    }
    if config.system_type is not LighthouseSystemType.UNKNOWN:
        result["systemType"] = int(config.system_type)
    return result


async def read_lighthouse_configuration(cf: Crazyflie) -> LighthouseConfiguration:
    """Reads the Lighthouse configuration of all base stations from a
    Crazyflie drone.

    Raises:
        NotSupportedError: if the drone does not support the Lighthouse system
    """
    lighthouse = cf.lighthouse
    lighthouse.number_of_base_stations = MAX_BASE_STATIONS
    try:
        return await lighthouse.get_configuration()
    except ValueError:
        # aiocflib throws ValueError if the drone has no Lighthouse memory
        raise NotSupportedError(
            "The drone does not support the Lighthouse system"
        ) from None


async def write_lighthouse_configuration(
    cf: Crazyflie, config: LighthouseConfiguration
) -> None:
    """Replaces the Lighthouse configuration of a Crazyflie drone and makes
    the changes permanent.

    Unlike the similar function of ``aiocflib``, this function also writes
    base stations that have only a geometry or only calibration data.

    Raises:
        NotSupportedError: if the drone does not support the Lighthouse system
    """
    lighthouse = cf.lighthouse
    lighthouse.number_of_base_stations = MAX_BASE_STATIONS
    try:
        await lighthouse.clear_all()
        if config.system_type is not LighthouseSystemType.UNKNOWN:
            await cf.param.set("lighthouse.systemType", int(config.system_type))
        await lighthouse.set_geometries(config.geometries)
        await lighthouse.set_calibrations(config.calibrations)
    except ValueError:
        # aiocflib throws ValueError if the drone has no Lighthouse memory
        raise NotSupportedError(
            "The drone does not support the Lighthouse system"
        ) from None

    await cf.param.set("lighthouse.bsCalibReset", 1)
    await lighthouse.persist()


def _create_anchor(index: int, geometry: LighthouseBsGeometry | None) -> Anchor:
    """Creates an anchor that represents the base station with the given
    zero-based index.

    Anchor IDs are one-based to match the channel numbers of the base
    stations.
    """
    anchor = Anchor(id=str(index + 1))
    if geometry is None:
        anchor.active = False
        anchor.errors.ensure(FlockwaveErrorCode.CONFIGURATION_ERROR)
    else:
        anchor.position = PositionXYZ(*geometry.origin)
        try:
            anchor.attitude = quaternion_from_rotation_matrix(geometry.rotation_matrix)
        except ValueError:
            anchor.errors.ensure(FlockwaveErrorCode.CONFIGURATION_ERROR)
    return anchor


def _to_bytes(
    items: Mapping[int, LighthouseBsGeometry] | Mapping[int, LighthouseBsCalibration],
) -> dict[int, bytes]:
    return {index: item.to_bytes() for index, item in items.items()}


_BASE_STATION_INDEX_PATTERN = "^(?:[0-9]|1[0-5])$"

_SWEEP_KEYS = ("phase", "tilt", "curve", "gibmag", "gibphase", "ogeemag", "ogeephase")

_VECTOR_SCHEMA = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}

CONFIGURATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "type": {"const": "lighthouse_system_configuration"},
        "systemType": {
            "type": "integer",
            "title": "Lighthouse system version",
            "enum": [int(LighthouseSystemType.V1), int(LighthouseSystemType.V2)],
        },
        "geos": {
            "type": "object",
            "title": "Base station geometries",
            "patternProperties": {
                _BASE_STATION_INDEX_PATTERN: {
                    "type": "object",
                    "properties": {
                        "origin": _VECTOR_SCHEMA,
                        "rotation": {
                            "type": "array",
                            "items": _VECTOR_SCHEMA,
                            "minItems": 3,
                            "maxItems": 3,
                        },
                    },
                    "required": ["origin", "rotation"],
                }
            },
            "additionalProperties": False,
        },
        "calibs": {
            "type": "object",
            "title": "Base station calibration data",
            "patternProperties": {
                _BASE_STATION_INDEX_PATTERN: {
                    "type": "object",
                    "properties": {
                        "sweeps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    key: {"type": "number"} for key in _SWEEP_KEYS
                                },
                                "required": list(_SWEEP_KEYS),
                            },
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "uid": {"type": "integer", "minimum": 0},
                    },
                    "required": ["sweeps", "uid"],
                }
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
"""JSON schema of the configuration of the Lighthouse LPS type."""
