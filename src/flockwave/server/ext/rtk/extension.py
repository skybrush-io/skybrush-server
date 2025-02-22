"""Extension that connects to one or more data sources for RTK connections
and forwards the corrections to the UAVs managed by the server.
"""

from __future__ import annotations

import json

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import partial
from pathlib import Path
from time import monotonic
from trio import CancelScope, open_memory_channel, open_nursery, sleep
from trio.abc import SendChannel
from trio_util import AsyncBool, periodic
from typing import Callable, cast, Any, ClassVar, Iterator, Optional, Union

from flockwave.channels import ParserChannel
from flockwave.connections import create_connection, RWConnection
from flockwave.gps.enums import GNSSType
from flockwave.gps.formatting import format_gps_coordinate_as_nmea_gga_message
from flockwave.gps.rtk import RTKMessageSet, RTKSurveySettings
from flockwave.gps.ubx.rtk_config import UBXRTKBaseConfigurator
from flockwave.gps.vectors import ECEFToGPSCoordinateTransformation, GPSCoordinate
from flockwave.server.ext.base import Extension
from flockwave.server.message_handlers import (
    create_mapper,
    create_multi_object_message_handler,
    create_object_listing_request_handler,
)
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import ConnectionPurpose, default_id_generator
from flockwave.server.model.log import Severity
from flockwave.server.model.messages import FlockwaveMessage, FlockwaveResponse
from flockwave.server.registries import find_in_registry
from flockwave.server.utils import overridden
from flockwave.server.utils.serial import (
    SerialPortConfiguration,
    SerialPortDescriptor,
    describe_serial_port,
    is_likely_not_rtk_base_station,
    list_serial_ports,
)

from .beacon_manager import RTKBeaconManager
from .clock_sync import GPSClockSynchronizationValidator
from .enums import MessageSet, RTKConfigurationPresetType
from .preset import (
    RTKConfigurationPreset,
    ALLOWED_FORMATS,
    describe_format,
)
from .registry import RTKPresetRegistry
from .statistics import RTKStatistics


@dataclass
class RTKPresetRequest:
    """Simple dataclass that stores the name of the last RTK preset that the
    user attempted to use, along with its timestamp.
    """

    preset_id: str
    timestamp: float = field(default_factory=monotonic)

    @property
    def age(self) -> float:
        """Returns the number of seconds elapsed since this request."""
        return max(0, monotonic() - self.timestamp)

    def touch(self) -> None:
        """Updates the timestamp of the request to the current timestamp."""
        self.timestamp = monotonic()


class RTKExtension(Extension):
    """Extension that connects to one or more data sources for RTK connections
    and forwards the corrections to the UAVs managed by the server.
    """

    RTK_PACKET_SIGNAL: ClassVar[str] = "rtk:packet"

    _clock_sync_validator: GPSClockSynchronizationValidator
    _current_preset: Optional[RTKConfigurationPreset] = None
    _custom_user_presets_file: str = ""
    _dynamic_serial_port_configurations: list[SerialPortConfiguration]
    _dynamic_serial_port_filters: list[str]
    _exclude_non_rtk_bases: bool = True
    _last_preset_request_from_user: Optional[RTKPresetRequest] = None
    _presets: list[RTKConfigurationPreset]
    _registry: Optional[RTKPresetRegistry] = None
    _message_set: MessageSet = MessageSet.FULL
    _rtk_beacon_manager: RTKBeaconManager
    _rtk_preset_task_cancel_scope: Optional[CancelScope] = None
    _rtk_survey_trigger: Optional[AsyncBool] = None
    _statistics: RTKStatistics
    _survey_settings: RTKSurveySettings
    _tx_queue: Optional[SendChannel] = None

    def __init__(self):
        """Constructor."""
        super().__init__()

        self._dynamic_serial_port_configurations = []
        self._dynamic_serial_port_filters = []
        self._presets = []

        self._clock_sync_validator = GPSClockSynchronizationValidator()
        self._rtk_beacon_manager = RTKBeaconManager()
        self._statistics = RTKStatistics()
        self._survey_settings = RTKSurveySettings()

    def configure(self, configuration: dict[str, Any]) -> None:
        """Loads the extension."""
        assert self.log

        self._id_format = configuration.get("id_format", "rtk:{0}")

        self._presets = []
        self._load_presets_from(configuration.get("presets", {}))

        self._custom_user_presets_file = str(configuration.get("presets_file", ""))
        if self._custom_user_presets_file:
            self.log.info(
                f"Loading RTK presets from {self._custom_user_presets_file!r}"
            )

        self._dynamic_serial_port_configurations = []
        serial_port_specs = configuration.get("add_serial_ports")
        if serial_port_specs is not None:
            serial_port_spec_list: list[Union[int, dict]]
            serial_port_specs_iter: Optional[Iterator[Union[int, dict]]] = None

            if serial_port_specs is True:
                serial_port_spec_list = [
                    115200
                ]  # standard baud rate for 433 MHz radios
            elif serial_port_specs is False:
                serial_port_spec_list = []
            elif isinstance(serial_port_specs, int):
                # we assume it is a single baud rate
                serial_port_spec_list = [serial_port_specs]
            else:
                serial_port_spec_list = serial_port_specs

            try:
                serial_port_specs_iter = iter(serial_port_spec_list)
            except Exception:
                self.log.error(
                    f"Ignoring invalid serial port configuration {serial_port_specs!r}"
                )

            if serial_port_specs_iter:
                for index, spec in enumerate(serial_port_specs_iter):
                    if isinstance(spec, int):
                        spec = {"baud": spec}
                    if isinstance(spec, dict):
                        self._dynamic_serial_port_configurations.append(spec)
                    else:
                        self.log.error(
                            f"Ignoring invalid serial port configuration at index #{index}"
                        )

        self._exclude_non_rtk_bases = bool(
            configuration.get("exclude_non_rtk_bases", True)
        )
        self._rtk_beacon_manager.enabled = bool(
            configuration.get("register_beacons", True)
        )

        serial_port_filters = configuration.get("exclude_serial_ports")
        if isinstance(serial_port_filters, str):
            serial_port_filters = [serial_port_filters]

        if serial_port_filters is None:
            self._dynamic_serial_port_filters = []
        elif hasattr(serial_port_filters, "__iter__"):
            self._dynamic_serial_port_filters = [
                str(filter) for filter in serial_port_filters
            ]
        else:
            self._dynamic_serial_port_filters = []

        self._survey_settings.accuracy = (
            int(configuration.get("survey_accuracy", 100)) / 100.0
        )

        self._survey_settings.duration = int(configuration.get("survey_duration", 60))

        self._survey_settings.message_set = (
            RTKMessageSet.MSM7
            if configuration.get("use_high_precision", True)
            else RTKMessageSet.MSM4
        )

        fixed = configuration.get("fixed")
        if isinstance(fixed, (list, tuple)):
            fixed = {"position": list(fixed)}

        if isinstance(fixed, dict) and "position" in fixed:
            if "accuracy" not in fixed:
                self.log.warning(
                    "Missing accuracy from fixed base station position "
                    "specification, assuming 1m"
                )
                accuracy = 1
            else:
                accuracy = float(fixed["accuracy"])  # type: ignore

            # Note: RTK extension configuration stores ECEF in meters,
            # while flockwave-gps>=3.0.0 stores ECEF coordinates in JSON in
            # millimeters. So we need to convert here from m to mm
            # to get it converted back to meters by `update_from_json()`
            self._survey_settings.update_from_json(
                {
                    "position": [int(round(p * 1000)) for p in fixed["position"]],
                    "accuracy": accuracy,
                }
            )

            position = self._survey_settings.position
            if position is not None:
                coord = ECEFToGPSCoordinateTransformation().to_gps(position).format()
                self.log.info(
                    f"Base station is fixed at {coord} (accuracy: {accuracy}m)"
                )
        elif fixed is not None:
            self.log.warning(
                f"Ignoring invalid fixed base station position specification: {fixed!r}"
            )

        gnss_types = configuration.get("gnss_types")
        if gnss_types and hasattr(gnss_types, "__contains__") and "all" in gnss_types:
            gnss_types = "all"
        if gnss_types == "all":
            gnss_types = None
        try:
            self._survey_settings.update_from_json({"gnssTypes": gnss_types})
        except ValueError:
            self.log.warning(
                f"Ignoring invalid GNSS type specification: {gnss_types!r}"
            )

        message_set = configuration.get("message_set")
        if message_set:
            try:
                self._message_set = MessageSet(message_set)
            except ValueError:
                self.log.warning(f"Ignoring unknown message set: {message_set!r}")

    @property
    def current_preset(self) -> Optional[RTKConfigurationPreset]:
        """Returns the currently selected RTK configuration preset that is used
        for broadcasting RTK corrections to connected UAVs.
        """
        return self._current_preset

    def delete_preset(self, preset: RTKConfigurationPreset) -> bool:
        """Deletes the RTK preset if it is a user preset.

        Returns:
            true, unconditionally

        Raises:
            RuntimeError: if the preset cannot be deleted or it does not exist
                in the registry
        """
        assert self._registry is not None

        if preset.type is not RTKConfigurationPresetType.USER:
            raise RuntimeError("Only user-defined presets can be deleted")

        existing = self._registry.remove(preset)
        if existing is None:
            raise RuntimeError("Preset does not exist")

        return True

    def exports(self) -> dict[str, Any]:
        return {
            "are_corrections_ok": self._are_corrections_ok,
            "get_current_preset": self._get_current_preset,
            "get_statistics": self._get_statistics,
        }

    def find_preset_by_id(
        self, preset_id: str, response: Optional[FlockwaveResponse] = None
    ) -> Optional[RTKConfigurationPreset]:
        """Finds the RTK preset with the given ID in the RTK preset registry or
        registers a failure in the given response object if there is no preset
        with the given ID.

        Parameters:
            preset_id: the ID of the preset to find
            response: the response in which the failure can be registered

        Returns:
            the RTK preset with the given ID or ``None`` if there is no such
            RTK preset
        """
        return find_in_registry(
            self._registry,
            preset_id,
            response=response,
            failure_reason="No such RTK preset",
        )

    def handle_RTK_NEW(
        self, message: FlockwaveMessage, sender, hub: MessageHub
    ) -> FlockwaveResponse:
        """Handles an incoming RTK-NEW message to create a new RTK preset."""
        assert self._registry is not None

        try:
            body = message.body or {}
            preset_spec = body.get("preset")
            if not isinstance(preset_spec, dict):
                raise RuntimeError("Preset does not match expected schema")

            if "id" in preset_spec:
                raise RuntimeError("Preset must not have an ID")

            if "type" in preset_spec:
                raise RuntimeError("Preset must not have a type")

            preset: RTKConfigurationPreset = RTKConfigurationPreset.from_json(
                preset_spec,
                id=default_id_generator(),
                type=RTKConfigurationPresetType.USER,
            )
            try:
                self._registry.add(preset)
            except KeyError:
                raise RuntimeError(f"Preset ID {preset.id} is already taken") from None

            return hub.create_response_to(message, {"id": preset.id})

        except Exception as ex:
            return hub.reject(message, str(ex))

    def handle_RTK_SAVE(
        self, message: FlockwaveMessage, sender, hub: MessageHub
    ) -> FlockwaveResponse:
        """Handles an incoming RTK-SAVE message to save all user presets to the
        configuration file storing the user-defined presets.
        """
        try:
            self._save_user_presets()
            return hub.acknowledge(message)
        except Exception as ex:
            return hub.reject(message, str(ex))

    def handle_RTK_SOURCE(
        self, message: FlockwaveMessage, sender, hub: MessageHub
    ) -> FlockwaveResponse:
        """Handles an incoming RTK-SOURCE message."""
        if "id" in message.body:
            # Selecting a new RTK source to use
            preset_id: Optional[str] = message.body["id"]
            if preset_id is None:
                desired_preset = None
            elif isinstance(preset_id, str):
                desired_preset = self.find_preset_by_id(preset_id)
                if desired_preset is None:
                    return hub.reject(message, reason="No such RTK preset")
            else:
                preset_id, desired_preset = None, None

            self._request_preset_switch_later(desired_preset)
            self._last_preset_request_from_user = (
                RTKPresetRequest(preset_id=preset_id)
                if preset_id and desired_preset
                else None
            )

            return hub.acknowledge(message)
        else:
            # Querying the currently used RTK source
            return hub.create_response_or_notification(
                body={"id": self.current_preset.id if self.current_preset else None},
                in_response_to=message,
            )

    def handle_RTK_SURVEY(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-SURVEY message."""
        if "settings" not in message.body:
            # Querying the current RTK survey settings
            return hub.create_response_or_notification(
                body={"settings": self.survey_settings},
                in_response_to=message,
            )
        else:
            # Updating the RTK survey settings and starting a new survey
            settings = message.body["settings"]
            error = None
            if isinstance(settings, dict):
                # HACK HACK HACK: if we have a fixed position from the config
                # file, don't update the accuracy
                if self.survey_settings.position is not None:
                    if "position" not in settings and "accuracy" in settings:
                        del settings["accuracy"]
                try:
                    self.survey_settings.update_from_json(settings)
                except ValueError as ex:
                    error = str(ex)
            else:
                error = "Settings object missing or invalid"

            if error is None:
                self._request_survey()

            return hub.acknowledge(message, outcome=error is None, reason=error)

    def handle_RTK_STAT(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-STAT message."""
        return self._statistics.json

    async def run(self, app, configuration, logger):
        hotplug_event = app.import_api("signals").get("hotplug:event")

        self._load_user_presets()

        with ExitStack() as stack:
            tx_queue, rx_queue = open_memory_channel(0)

            stack.enter_context(
                overridden(
                    self,
                    _current_preset=None,
                    _registry=RTKPresetRegistry(),
                    _rtk_preset_task_cancel_scope=None,
                    _rtk_survey_trigger=AsyncBool(False),
                    _tx_queue=tx_queue,
                )
            )
            stack.enter_context(hotplug_event.connected_to(self._on_hotplug_event))

            assert self._registry is not None

            for preset in self._presets:
                self._registry.add(preset)

            self._update_dynamic_presets(first=True)

            # Register message handlers for RTK-related message
            handle_RTK_INF = create_mapper(
                "RTK-INF",
                self._registry,
                key="preset",
                description="RTK preset",
            )
            handle_RTK_UPDATE = create_mapper(
                "RTK-UPDATE",
                self._registry,
                context_getter=self._get_updates_from_RTK_UPDATE_message,
                getter=self.update_preset,
                description="RTK preset",
            )
            handle_RTK_DEL = create_mapper(
                "RTK-DEL",
                self._registry,
                getter=self.delete_preset,
                description="RTK preset",
            )
            stack.enter_context(
                app.message_hub.use_message_handlers(
                    {
                        "RTK-INF": create_multi_object_message_handler(handle_RTK_INF),
                        "RTK-LIST": create_object_listing_request_handler(
                            self._registry
                        ),
                        "RTK-STAT": self.handle_RTK_STAT,
                        "RTK-SOURCE": self.handle_RTK_SOURCE,
                        "RTK-SURVEY": self.handle_RTK_SURVEY,
                    },
                    used_to_be_experimental=True,
                )
            )
            stack.enter_context(
                app.message_hub.use_message_handlers(
                    {
                        "X-RTK-DEL": create_multi_object_message_handler(
                            handle_RTK_DEL
                        ),
                        "X-RTK-NEW": self.handle_RTK_NEW,
                        "X-RTK-SAVE": self.handle_RTK_SAVE,
                        "X-RTK-UPDATE": create_multi_object_message_handler(
                            handle_RTK_UPDATE
                        ),
                    },
                )
            )

            # If we have a preset that should be selected at startup, do it now
            for preset in self._presets:
                if preset.auto_select:
                    self._request_preset_switch_later(preset)
                    break

            async with self.use_nursery():
                async for message, args in rx_queue:
                    if message == "set_preset":
                        preset = cast(Optional[RTKConfigurationPreset], args)
                        await self._perform_preset_switch(preset)

    def update_preset(
        self,
        preset: RTKConfigurationPreset,
        updates: Union[
            dict[str, Any], Callable[[RTKConfigurationPreset], dict[str, Any]]
        ],
    ) -> bool:
        """Updates the given RTK preset.

        Args:
            preset: the preset to update. It is assumed to have been retrieved
                from the preset registry.
            updates: either a dictionary mapping the fields of the preset to
                update to their new values, or a function that can be called
                with the preset and returns such a dictionary. The latter is
                needed to allow this function to be used by `create_mapper()`

        Returns:
            true, unconditionally
        """
        assert self._registry is not None

        if preset.type is not RTKConfigurationPresetType.USER:
            raise RuntimeError("Only user-defined presets can be deleted")

        if callable(updates):
            updates = updates(preset)

        preset.update_from(updates)
        return True

    @property
    def survey_settings(self) -> RTKSurveySettings:
        """Returns the current survey settings of the RTK extension."""
        return self._survey_settings

    @property
    def user_presets_file(self) -> Path:
        """Full path of the file that stores the user presets."""
        try:
            if self._custom_user_presets_file:
                return Path(self._custom_user_presets_file)
        except Exception:
            pass

        return Path(self.get_data_dir()) / "presets.json"

    def _are_corrections_ok(
        self, *, min_satellite_count: int = 8, max_age: float = 8
    ) -> bool:
        """Returns whether the current status of the extension indicates that
        RTK corrections are being received correctly from the base station.

        This function is exported to other extensions via the exports object.

        Parameters:
            min_satellite_count: minimum number of satellites for which we would
                like to have correction data
            max_age: maximum number of seconds that may pass without up-to-date
                antenna position information
        """
        return (
            self._get_current_preset() is not None
            and self._get_statistics().are_corrections_ok(
                min_satellite_count=min_satellite_count, max_age=max_age
            )
        )

    def _get_current_preset(self) -> Optional[RTKConfigurationPreset]:
        """Returns the current RTK configuration preset.

        This function is exported to other extensions via the exports object.
        """
        return self._current_preset

    def _get_statistics(self) -> RTKStatistics:
        """Returns the statistics of the RTK messages being received.

        This function is exported to other extensions via the exports object.
        """
        return self._statistics

    def _get_updates_from_RTK_UPDATE_message(
        self, message: Optional[FlockwaveMessage]
    ) -> Callable[[RTKConfigurationPreset], dict[str, Any]]:
        """Helper function for the implementation of the RTK-UPDATE message
        handler.

        Given an RTK-UPDATE message, extracts the object describing the updates
        to apply to the individual RTK presets.

        Returns:
            a function that can be called with an RTK configuration preset
            being updated and that returns the updates to perform on the preset
        """
        assert message is not None
        updates: dict[str, dict[str, Any]] = message.body.get("updates") or {}

        def update_getter(preset: RTKConfigurationPreset) -> dict[str, Any]:
            update = updates.get(preset.id) or {}
            return update

        return update_getter

    def _load_presets_from(
        self,
        obj: Union[dict[str, Any], Sequence[Any]],
        *,
        type: RTKConfigurationPresetType = RTKConfigurationPresetType.BUILTIN,
    ):
        """Loads RTK configuration presets from the given object, which must be
        a dictionary or a sequence.

        When the object is a dictionary, it is assumed that it maps preset IDs
        to the JSON descriptions of the presets themselves. The JSON descriptions
        do not need to contain an ID (and if they do, they are ignored). When
        the object is a list, it is assumed that each entry is a JSON
        description of the preset and the description contains a key named
        ``id``, which serves as the ID of the preset.
        """
        assert self.log

        if isinstance(obj, dict):
            for id, spec in obj.items():
                try:
                    preset = RTKConfigurationPreset.from_json(spec, id=id, type=type)
                except Exception:
                    self.log.error(f"Ignoring invalid RTK configuration {id!r}")
                    continue

                self._presets.append(preset)

        elif isinstance(obj, Sequence):
            for spec in obj:
                id = spec.get("id")
                if id is None:
                    self.log.error("Skipping preset with no ID")
                    continue

                try:
                    preset = RTKConfigurationPreset.from_json(spec, id=id, type=type)
                except Exception:
                    self.log.error(f"Ignoring invalid RTK configuration {id!r}")
                    continue

                self._presets.append(preset)

    def _load_user_presets(self) -> None:
        assert self.log

        user_presets = {}
        user_preset_file = self.user_presets_file
        if user_preset_file.exists():
            with user_preset_file.open() as fp:
                try:
                    user_presets = json.load(fp)
                except Exception:
                    self.log.error(
                        f"Parse error while reading user presets from {str(user_preset_file)!r}"
                    )

        self._load_presets_from(user_presets, type=RTKConfigurationPresetType.USER)

    def _save_user_presets(self) -> None:
        assert self.log
        assert self._registry

        user_preset_file = self.user_presets_file
        user_presets = {
            preset_id: self._registry.find_by_id(preset_id).json
            for preset_id in self._registry.ids
            if self._registry.find_by_id(preset_id).type
            is RTKConfigurationPresetType.USER
        }

        with user_preset_file.open("w") as fp:
            try:
                json.dump(user_presets, fp, indent=2, sort_keys=True)
            except Exception:
                self.log.error(
                    f"Error while writing user presets to {str(user_preset_file)!r}"
                )
                raise

    async def _request_preset_switch(
        self, value: Optional[RTKConfigurationPreset]
    ) -> None:
        """Requests the extension to switch to a new RTK preset."""
        if not self._tx_queue:
            if self.log:
                self.log.warning(
                    "Cannot set RTK preset when the extension is not running"
                )
        else:
            await self._tx_queue.send(("set_preset", value))

    def _request_preset_switch_later(
        self, value: Optional[RTKConfigurationPreset]
    ) -> None:
        """Requests the extension to switch to a new RTK preset as soon as
        possible (but not immediately).
        """
        assert self.app is not None
        self.app.run_in_background(self._request_preset_switch, value)

    def _request_survey(self) -> None:
        """Requests the extension to start a new survey process on the
        current RTK connection.
        """
        if not self._rtk_survey_trigger:
            if self.log:
                self.log.warning(
                    "Cannot set RTK preset when the extension is not running"
                )
        else:
            self._rtk_survey_trigger.value = True

    async def _perform_preset_switch(
        self, value: Optional[RTKConfigurationPreset]
    ) -> None:
        """Performs the switch from an RTK configuration preset to another,
        cleaning up the old connection and creating a new one.
        """
        if self._current_preset is value:
            return

        if self._rtk_preset_task_cancel_scope is not None:
            self._rtk_preset_task_cancel_scope.cancel()
            self._rtk_preset_task_cancel_scope = None
            self._current_preset = None

        self._current_preset = value

        assert self._nursery is not None

        if value is not None:
            self._rtk_preset_task_cancel_scope = await self._nursery.start(
                self._run_connections_for_preset, value
            )

    async def _run_survey(self, preset, connection, *, task_status) -> None:
        with CancelScope() as scope:
            task_status.started(scope)

            duration = self._survey_settings.duration
            accuracy = self._survey_settings.accuracy
            position = self._survey_settings.position
            accuracy_cm = int(accuracy * 100)

            need_survey = position is None

            configurator = UBXRTKBaseConfigurator(self._survey_settings)

            if self.log:
                if position is not None:
                    coord = (
                        ECEFToGPSCoordinateTransformation().to_gps(position).format()
                    )
                    self.log.info(
                        f"Configuring RTK base station to fixed coordinate: "
                        f"{coord}, accuracy is {accuracy_cm} cm"
                    )
                else:
                    self.log.info(
                        f"Starting survey for {preset.title!r} for at least {duration} "
                        f"seconds, desired accuracy is {accuracy_cm} cm",
                    )

            success = False
            try:
                await connection.wait_until_connected()
                await configurator.run(connection.write, sleep)
                success = True
            except Exception:
                if self.log:
                    self.log.exception(
                        f"Unexpected exception while setting up survey for "
                        f"{preset.title!r}"
                    )
            finally:
                if self.log:
                    if need_survey:
                        if success:
                            self.log.info(
                                f"Started survey for {preset.title!r}",
                                extra={"semantics": "success"},
                            )
                        else:
                            self.log.error(
                                f"Failed to start survey for {preset.title!r}",
                                extra={"telemetry": "ignore"},
                            )
                    else:
                        if success:
                            self.log.info(
                                f"{preset.title!r} configured successfully",
                                extra={"semantics": "success"},
                            )
                            self._statistics.set_to_fixed_with_accuracy(
                                accuracy_cm / 100.0
                            )
                        else:
                            self.log.error(
                                f"Failed to configure {preset.title!r}",
                                extra={"telemetry": "ignore"},
                            )

    async def _run_connections_for_preset(
        self, preset: RTKConfigurationPreset, *, task_status
    ) -> None:
        """Master task that handles all the connections that constitute a single
        RTK preset.
        """
        assert self.app is not None

        self._clock_sync_validator.assume_sync()

        with ExitStack() as stack:
            assert self._rtk_survey_trigger is not None
            assert self.app is not None

            async with open_nursery() as nursery:
                stack.enter_context(self._statistics.use())
                stack.enter_context(self._rtk_beacon_manager.use(self, nursery))
                stack.enter_context(
                    self._clock_sync_validator.sync_state_changed.connected_to(
                        self._on_gps_clock_sync_state_changed,
                        sender=self._clock_sync_validator,
                    )  # type: ignore
                )

                self._rtk_survey_trigger.value = preset.auto_survey

                task_status.started(nursery.cancel_scope)

                connections = []
                for source in preset.sources:
                    try:
                        connection = create_connection(source)
                        connections.append(connection)
                        stack.enter_context(
                            self.app.connection_registry.use(
                                connection,
                                self._id_format.format(preset.id),
                                (
                                    f"RTK corrections ({preset.title})"
                                    if preset.title
                                    else "RTK corrections"
                                ),
                                ConnectionPurpose.dgps,  # type: ignore
                            )
                        )
                        nursery.start_soon(
                            partial(
                                self.app.supervise,
                                connection,
                                task=partial(
                                    self._run_single_connection_for_preset,
                                    preset=preset,
                                ),  # type: ignore
                            )
                        )
                    except Exception:
                        if self.log:
                            self.log.exception(
                                "Unexpected error while creating RTK connection"
                            )

                survey_task = None
                while True:
                    await self._rtk_survey_trigger.wait_value(True)
                    self._rtk_survey_trigger.value = False

                    # Cancel the previous survey attempt (if any), and then
                    # start a new, cancellable survey procedure
                    if survey_task is not None:
                        survey_task.cancel()
                        # Give some time for the previous task to end
                        await sleep(0.1)

                    # Currently we always target the first connection with
                    # the messages that attempt to start the survey.
                    # This might change later. Also, survey is supported only
                    # for U-blox receivers and autodetected connections at the moment.
                    if preset.format in ("auto", "ubx") and connections:
                        survey_task = await nursery.start(
                            self._run_survey, preset, connections[0]
                        )

    async def _run_single_connection_for_preset(
        self, connection: RWConnection[bytes, bytes], *, preset: RTKConfigurationPreset
    ) -> None:
        """Task that reads messages from a single connection related to an
        RTK preset.
        """
        assert self.app is not None

        channel = ParserChannel(connection, parser=preset.create_gps_parser())  # type: ignore
        signal = self.app.import_api("signals").get(self.RTK_PACKET_SIGNAL)
        get_location = self.app.import_api("location").get_location
        rtcm_encoder = preset.create_rtcm_encoder()
        maybe_vrs = "NTRIPConnection" in str(connection.__class__)

        async def handle_incoming_packets():
            """Handles incoming RTK correction packets from the connection."""
            async for packet in channel:
                accepted = preset.accepts(packet) and self._message_set.accepts(packet)

                self._clock_sync_validator.notify(packet)
                self._statistics.notify(packet, forwarded=accepted)

                if accepted:
                    encoded = rtcm_encoder(packet)  # type: ignore
                    signal.send(packet=encoded)

        async def generate_vrs_packets():
            """Generates messages that have to be provided if the connection is
            a virtual reference station (VRS).
            """
            if not callable(get_location):
                return

            async for _ in periodic(5):
                location = get_location()
                if location is None or location.position is None:
                    continue

                lat: float = location.position.lat
                lon: float = location.position.lon
                alt: Optional[float] = location.position.amsl
                if alt is None:
                    alt = 0.0

                lat_dms, lat_min = divmod(lat, 1)
                lat_dms = abs(lat_dms + lat_min * 0.6) * 100

                lon_dms, lon_min = divmod(lon, 1)
                lon_dms = abs(lon_dms + lon_min * 0.6) * 100

                message = format_gps_coordinate_as_nmea_gga_message(
                    GPSCoordinate(lat, lon, amsl=alt)
                )
                await connection.write(message.encode("ascii"))

        async with channel:
            if maybe_vrs:
                async with open_nursery() as nursery:
                    nursery.start_soon(generate_vrs_packets)
                    nursery.start_soon(handle_incoming_packets)
            else:
                await handle_incoming_packets()

    def _on_gps_clock_sync_state_changed(self, sender, in_sync: bool) -> None:
        """Handler called when the extension detects that the GPS clock is
        out of sync with the server, or when the clocks are in sync again.
        """
        if not self.app:
            return

        send_message = self.app.request_to_send_SYS_MSG_message
        if in_sync:
            send_message("GPS clock and server clock are now in sync.")
        else:
            send_message(
                "Server clock is not synchronized to GPS clock. Please sync "
                "the date and time on the server to a reliable time source.",
                severity=Severity.WARNING,
            )

    def _on_hotplug_event(self, sender, event) -> None:
        """Handler called for hotplug events. Used to trigger the regeneration
        of the presets generated dynamically from serial ports.
        """
        self._update_dynamic_presets()

    def _should_use_serial_port_as_dynamic_preset(
        self, port: SerialPortDescriptor
    ) -> bool:
        """Returns whether the given serial port should appear as a dynamic
        preset in the list of RTK sources offered by the extension.
        """
        if self._exclude_non_rtk_bases and is_likely_not_rtk_base_station(port):
            return False

        if not self._dynamic_serial_port_filters:
            return True

        device = str(getattr(port, "device", "") or "")
        label = describe_serial_port(port)
        for pattern in self._dynamic_serial_port_filters:
            if fnmatch(device, pattern) or fnmatch(label, pattern):
                return False

        return True

    def _update_dynamic_presets(self, first: bool = False) -> None:
        """Enumerates all the serial ports on the computer and creates a list of
        dynamic presets, one or more for each serial port.

        Parameters:
            first: whether the list of dynamic presets is being updated for the
                first time during the initialization of the extension
        """
        if self._registry is None:
            return

        to_add = []
        seen = set()

        # List the serial ports, create presets for the new ones, remember the
        # ones for which we have already created a preset
        has_multiple_configurations = len(self._dynamic_serial_port_configurations) > 1
        for port in list_serial_ports():
            if not self._should_use_serial_port_as_dynamic_preset(port):
                continue

            for index, spec in enumerate(self._dynamic_serial_port_configurations):
                preset_id = self._get_dynamic_preset_id_for_serial_port(port, index)
                if self.find_preset_by_id(preset_id):
                    # This preset exists already, nothing to do
                    seen.add(preset_id)
                else:
                    preset = RTKConfigurationPreset.from_serial_port(
                        port,
                        spec,
                        id=preset_id,
                        use_configuration_in_title=has_multiple_configurations,
                    )
                    to_add.append(preset)

        to_remove = [
            existing_preset
            for existing_preset in self._registry
            if existing_preset.dynamic and existing_preset.id not in seen
        ]

        for preset in to_remove:
            self._registry.remove(preset)
            if self.log:
                self.log.info(
                    f"Removing RTK preset {preset.title!r} because the device was unplugged"
                )

        for preset in to_add:
            self._registry.add(preset)
            if not first:
                if self.log:
                    self.log.info(
                        f"Added new RTK preset {preset.title!r} for serial port"
                    )

        current_preset = self.current_preset

        if current_preset:
            # If the currently used RTK preset is gone (probably because the user
            # unplugged the device), switch to not using a preset
            if not self._registry or current_preset.id not in self._registry:
                self._request_preset_switch_later(None)
        else:
            # If we do not have a selected preset yet, but we remember the name
            # that the user explicitly requested for the last time, the request
            # was in the last 10 seocnds, _and_ this preset has re-appeared,
            # re-activate the preset. This helps with situations when the RTK
            # base station is plugged into an unpowered USB hub and the device
            # goes away for a few seconds
            req = self._last_preset_request_from_user
            if req and req.age < 30:
                last_used_preset = self.find_preset_by_id(req.preset_id)
                if last_used_preset is not None:
                    if self.log:
                        self.log.info(
                            f"Re-connecting to RTK preset {last_used_preset.title!r}"
                        )
                    req.touch()
                    self._request_preset_switch_later(last_used_preset)

    @staticmethod
    def _get_dynamic_preset_id_for_serial_port(port, index: int = 0) -> str:
        from flockwave.spec.ids import make_valid_object_id

        return make_valid_object_id(f"{port.device}/{index}")


construct = RTKExtension
dependencies = ("beacon", "location", "ntrip", "signals")
description = "Support for RTK base stations and external RTK correction sources"
optional_dependencies = {
    "hotplug": "detects when new USB devices are plugged in and updates the RTK sources automatically",
}


def get_schema():
    return {
        "properties": {
            "presets": {
                "type": "object",
                "title": "RTK base stations",
                "description": (
                    "Specifications of external RTK data sources that are provided "
                    "by the server even if no RTK base stations are connected"
                ),
                "propertyOrder": 2000,
                "options": {"disable_properties": False},
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "source": {"title": "Connection URL", "type": "string"},
                        "title": {
                            "title": "Title",
                            "type": "string",
                            "description": "Human-readable title used on the user interface",
                        },
                        "format": {
                            "title": "Message format",
                            "type": "string",
                            "enum": ALLOWED_FORMATS,
                            "default": "auto",
                            "options": {
                                "enum_titles": [
                                    describe_format(fmt) for fmt in ALLOWED_FORMATS
                                ]
                            },
                            "required": False,
                            "propertyOrder": 2000,
                        },
                        "filter": {
                            "type": "object",
                            "title": "Message filter",
                            "properties": {
                                "reject": {
                                    "type": "array",
                                    "title": "Reject messages",
                                    "description": (
                                        "Reject messages with the given IDs. IDs are in the following format: rtcm2/X or rtcm3/X where X is the numeric identifier of the RTCMv2 or RTCMv3 message"
                                    ),
                                    "format": "table",
                                    "items": {"type": "string"},
                                    "required": False,
                                },
                                "accept": {
                                    "type": "array",
                                    "title": "Accept messages",
                                    "description": (
                                        "Accept messages with the given IDs. IDs are in the following format: rtcm2/X or rtcm3/X where X is the numeric identifier of the RTCMv2 or RTCMv3 message"
                                    ),
                                    "format": "table",
                                    "items": {"type": "string"},
                                    "required": False,
                                },
                            },
                            "required": False,
                            "propertyOrder": 3000,
                        },
                        "auto_select": {
                            "type": "boolean",
                            "title": "Select automatically at startup",
                            "default": False,
                            "propertyOrder": 2500,
                            "format": "checkbox",
                        },
                        "auto_survey": {
                            "type": "boolean",
                            "title": "Start survey automatically when selected",
                            "default": False,
                            "propertyOrder": 2750,
                            "format": "checkbox",
                        },
                    },
                },
            },
            # presets_file intentionally left out; we do not want to allow the
            # user to mess with the location of the presets file by default,
            # but it is useful to us in certain deployments. Use it at your
            # own risk.
            "add_serial_ports": {
                "title": "Use serial ports automatically",
                "description": (
                    "Automatically offer serial ports as RTK sources with the given "
                    "baud rates"
                ),
                "type": "array",
                "format": "table",
                "items": {"type": "integer"},
                "required": False,
            },
            "exclude_serial_ports": {
                "title": "Exclude serial ports",
                "description": (
                    "Exclude serial ports matching the given wildcard patterns "
                    "from considering them as RTK base stations"
                ),
                "type": "array",
                "format": "table",
                "items": {"type": "string"},
                "required": False,
            },
            "exclude_non_rtk_bases": {
                "title": "Exclude devices that are known not to be RTK base stations",
                "description": (
                    "Matches each serial port against a hardcoded list of devices that are "
                    "known not to be RTK base stations and excludes ports that are on the "
                    "list. Typically you should not need to uncheck this option."
                ),
                "type": "boolean",
                "default": True,
                "format": "checkbox",
            },
            "fixed": {
                "title": "Use fixed base station coordinate",
                "description": (
                    "Base station cooordinates and accuracy to use when auto-configuring "
                    "an RTK base station. Uncheck to perform an automatic survey-in if "
                    "the RTK base station supports it."
                ),
                "type": "object",
                "properties": {
                    "position": {
                        "title": "Position",
                        "description": "Use ECEF coordinates (Earth centered, Earth fixed), in meters",
                        "minItems": 3,
                        "maxItems": 3,
                        "type": "array",
                        "format": "table",
                        "items": {"type": "number"},
                        "propertyOrder": 1000,
                    },
                    "accuracy": {
                        "title": "Accuracy",
                        "description": "Accuracy of the measured coordinates, in meters",
                        "type": "number",
                        "minValue": 0,
                        "default": 1,
                        "propertyOrder": 2000,
                    },
                },
                "propertyOrder": 3000,
                "required": False,
            },
            "gnss_types": {
                "title": "Use only selected GNSS types",
                "description": (
                    "GNSS types to request corrections for when auto-configuring an "
                    "RTK base station. Uncheck to request corrections for all GNSS "
                    "types."
                ),
                "type": "array",
                "format": "checkbox",
                "items": {
                    "type": "string",
                    "enum": ["all"] + [e.value for e in GNSSType],
                    "options": {
                        "enum_titles": ["All GNSS types"]
                        + [e.describe() for e in GNSSType],
                    },
                },
                "uniqueItems": True,
                "required": False,
            },
            "message_set": {
                "type": "string",
                "title": "Message set",
                "description": (
                    "Specifies the set of messages to forward to UAVs and other "
                    "components in the server. 'Basic' is the minimal set of "
                    "RTCM messages that are needed for RTK corrections on the "
                    "UAVs. 'Full' will forward all messages unconditionally."
                ),
                "enum": ["basic", "full"],
                "options": {
                    "enum_titles": ["Basic RTCM (for RTK corrections)", "Full"]
                },
                "default": "basic",
            },
            "register_beacons": {
                "type": "boolean",
                "title": "Register the RTK base station as a beacon",
                "description": (
                    "Registers the current RTK base station as a beacon in the server. "
                    "This allows frontends like Skybrush Live to show the position of "
                    "the RTK base station on the map."
                ),
                "default": True,
                "format": "checkbox",
            },
            "survey_accuracy": {
                "type": "number",
                "minValue": 1,
                "title": "Desired accuracy of RTK surveys [cm]",
                "description": (
                    "Desired accuracy of RTK surveys for locally connected base "
                    "stations that need auto-configuration, in centimeters. "
                    "Supported for base stations using uBlox chipsets."
                ),
                "default": 100,
            },
            "survey_duration": {
                "type": "integer",
                "minValue": 0,
                "title": "Minimum duration of RTK surveys [s]",
                "description": (
                    "Minimum duration of RTK surveys for locally connected base "
                    "stations that need auto-configuration, in seconds. Supported "
                    "for base stations using uBlox chipsets."
                ),
                "default": 60,
            },
            "use_high_precision": {
                "type": "boolean",
                "title": "Use high-precision MSM7 messages",
                "description": (
                    "Request corrections in high-precision MSM7 RTCM3 messages "
                    "when auto-configuring an RTK base station. Uncheck if the "
                    "rover(s) support MSM4 messages only."
                ),
                "default": True,
                "format": "checkbox",
            },
        }
    }
