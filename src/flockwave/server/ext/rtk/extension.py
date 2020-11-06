"""Extension that connects to one or more data sources for RTK connections
and forwards the corrections to the UAVs managed by the server.
"""

from contextlib import ExitStack
from functools import partial
from trio import open_memory_channel, open_nursery, sleep_forever
from typing import Optional

from flockwave.channels import ParserChannel
from flockwave.connections import Connection, create_connection
from flockwave.server.message_hub import MessageHub
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.messages import FlockwaveMessage
from flockwave.server.registries import find_in_registry
from flockwave.server.utils import overridden
from flockwave.server.utils.serial import (
    list_serial_ports,
)

from ..base import ExtensionBase

from .preset import RTKConfigurationPreset
from .registry import RTKPresetRegistry
from .statistics import RTKStatistics


class RTKExtension(ExtensionBase):
    """Extension that connects to one or more data sources for RTK connections
    and forwards the corrections to the UAVs managed by the server.
    """

    def __init__(self):
        """Constructor."""
        super().__init__()

        self._command_queue = None
        self._current_preset = None
        self._dynamic_serial_port_configurations = []
        self._presets = []
        self._registry = None
        self._statistics = RTKStatistics()
        self._rtk_preset_task_cancel_scope = None

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "rtk:{0}")
        self._presets = []
        for id, spec in configuration.get("presets", {}).items():
            try:
                self._presets.append(RTKConfigurationPreset.from_json(spec, id=id))
            except Exception:
                self.log.error(f"Ignoring invalid RTK configuration {id!r}")

        self._dynamic_serial_port_configurations = []
        serial_port_specs = configuration.get("add_serial_ports")
        if serial_port_specs is not None:
            if serial_port_specs is True:
                serial_port_specs = [115200]  # standard baud rate for 433 MHz radios
            elif serial_port_specs is False:
                serial_port_specs = []

            if isinstance(serial_port_specs, int):
                # we assume it is a single baud rate
                serial_port_specs = [serial_port_specs]

            try:
                serial_port_specs = iter(serial_port_specs)
            except Exception:
                self.log.error(
                    f"Ignoring invalid serial port configuration {serial_port_specs!r}"
                )
                serial_port_specs = None

            for index, spec in enumerate(serial_port_specs):
                if isinstance(spec, int):
                    spec = {"baud": spec}
                if isinstance(spec, dict):
                    self._dynamic_serial_port_configurations.append(spec)
                else:
                    self.log.error(
                        f"Ignoring invalid serial port configuration at index #{index}"
                    )

    @property
    def current_preset(self) -> Optional[RTKConfigurationPreset]:
        """Returns the currently selected RTK configuration preset that is used
        for broadcasting RTK corrections to connected UAVs.
        """
        return self._current_preset

    def find_preset_by_id(
        self, preset_id: str, response: Optional[FlockwaveMessage] = None
    ):
        """Finds the RTK preset with the given ID in the RTK preset registry or
        registers a failure in the given response object if there is no preset
        with the given ID.

        Parameters:
            preset_id (str): the ID of the preset to find
            response (Optional[FlockwaveResponse]): the response in which
                the failure can be registered

        Returns:
            Optional[RTKConfigurationPreset]: the RTK preset with the given ID
                or ``None`` if there is no such RTK preset
        """
        return find_in_registry(
            self._registry,
            preset_id,
            response=response,
            failure_reason="No such RTK preset",
        )

    def handle_RTK_INF(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-INF message."""
        presets = {}

        body = {"preset": presets, "type": "X-RTK-INF"}
        response = hub.create_response_or_notification(
            body=body, in_response_to=message
        )

        for preset_id in message.body["ids"]:
            entry = self.find_preset_by_id(preset_id, response)
            if entry:
                presets[preset_id] = entry.json

        return response

    def handle_RTK_LIST(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-LIST message."""
        return hub.create_response_or_notification(
            body={"ids": self._registry.ids}, in_response_to=message
        )

    def handle_RTK_SOURCE(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-SOURCE message."""
        if "id" in message.body:
            # Selecting a new RTK source to use
            if message.body["id"] is None:
                desired_preset = None
            else:
                desired_preset = self.find_preset_by_id(message.body["id"])
                if desired_preset is None:
                    return hub.reject(message, reason="No such RTK preset")

            self._request_preset_switch_later(desired_preset)

            return hub.acknowledge(message)
        else:
            # Querying the currently used RTK source
            return hub.create_response_or_notification(
                body={"id": self.current_preset.id if self.current_preset else None},
                in_response_to=message,
            )

    def handle_RTK_STAT(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-STAT message."""
        return self._statistics.json

    async def run(self, app, configuration, logger):
        hotplug_event = app.import_api("signals").get("hotplug:event")

        with ExitStack() as stack:
            tx_queue, rx_queue = open_memory_channel(0)

            stack.enter_context(
                overridden(
                    self,
                    _current_preset=None,
                    _registry=RTKPresetRegistry(),
                    _rtk_preset_task_cancel_scope=None,
                    _tx_queue=tx_queue,
                )
            )
            stack.enter_context(hotplug_event.connected_to(self._on_hotplug_event))

            for preset in self._presets:
                self._registry.add(preset)

            self._update_dynamic_presets(first=True)

            stack.enter_context(
                app.message_hub.use_message_handlers(
                    {
                        "X-RTK-INF": self.handle_RTK_INF,
                        "X-RTK-LIST": self.handle_RTK_LIST,
                        "X-RTK-STAT": self.handle_RTK_STAT,
                        "X-RTK-SOURCE": self.handle_RTK_SOURCE,
                    }
                )
            )

            async with self.use_nursery():
                async for message, args in rx_queue:
                    if message == "set_preset":
                        await self._perform_preset_switch(args)

    async def _request_preset_switch(
        self, value: Optional[RTKConfigurationPreset]
    ) -> None:
        """Requests the extension to switch to a new RTK preset."""
        if not self._tx_queue:
            self.log.warning("Cannot set RTK preset when the extension is not running")
        else:
            await self._tx_queue.send(("set_preset", value))

    def _request_preset_switch_later(
        self, value: Optional[RTKConfigurationPreset]
    ) -> None:
        """Requests the extension to switch to a new RTK preset as soon as
        possible (but not immediately).
        """
        self.app.run_in_background(self._request_preset_switch, value)

    async def _perform_preset_switch(self, value: RTKConfigurationPreset) -> None:
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

        if value is not None:
            self._rtk_preset_task_cancel_scope = await self._nursery.start(
                self._run_connections_for_preset, value
            )

    async def _run_connections_for_preset(
        self, preset: RTKConfigurationPreset, *, task_status
    ) -> None:
        """Master task that handles all the connections that constitute a single
        RTK preset.
        """
        with ExitStack() as stack:
            stack.enter_context(self._statistics.use())

            async with open_nursery() as nursery:
                task_status.started(nursery.cancel_scope)

                for source in preset.sources:
                    try:
                        connection = create_connection(source)
                        stack.enter_context(
                            self.app.connection_registry.use(
                                connection,
                                self._id_format.format(preset.id),
                                f"RTK corrections ({preset.title})"
                                if preset.title
                                else "RTK corrections",
                                ConnectionPurpose.dgps,
                            )
                        )
                        nursery.start_soon(
                            partial(
                                self.app.supervise,
                                connection,
                                task=partial(
                                    self._run_single_connection_for_preset,
                                    preset=preset,
                                ),
                            )
                        )
                    except Exception:
                        self.log.exception(
                            "Unexpected error while creating RTK connection"
                        )

                await sleep_forever()

    async def _run_single_connection_for_preset(
        self, connection: Connection, *, preset: RTKConfigurationPreset
    ) -> None:
        """Task that reads messages from a single connection related to an
        RTK preset.
        """
        channel = ParserChannel(connection, parser=preset.create_parser())
        signal = self.app.import_api("signals").get("rtk:packet")
        encoder = preset.create_encoder()

        async with channel:
            async for packet in channel:
                self._statistics.notify(packet)
                if preset.accepts(packet):
                    encoded = encoder(packet)
                    signal.send(packet=encoded)

    def _on_hotplug_event(self, sender, event) -> None:
        """Handler called for hotplug events. Used to trigger the regeneration
        of the presets generated dynamically from serial ports.
        """
        self._update_dynamic_presets()

    def _update_dynamic_presets(self, first: bool = False) -> None:
        """Enumerates all the serial ports on the computer and creates a list of
        dynamic presets, one or more for each serial port.

        Parameters:
            first: whether the list of dynamic presets is being updated for the
                first time during the initialization of the extension
        """
        if not self._dynamic_serial_port_configurations:
            return

        to_add = []
        seen = set()

        # List the serial ports, create presets for the new ones, remember the
        # ones for which we have already created a preset
        has_multiple_configurations = len(self._dynamic_serial_port_configurations) > 1
        for port in list_serial_ports():
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
                    preset.dynamic = True
                    to_add.append(preset)

        to_remove = [
            existing_preset
            for existing_preset in self._registry
            if existing_preset.dynamic and existing_preset.id not in seen
        ]

        for preset in to_remove:
            self._registry.remove(preset)
            self.log.info(
                f"Removing RTK preset {preset.title!r} because the device was unplugged"
            )

        for preset in to_add:
            self._registry.add(preset)
            if not first:
                self.log.info(f"Added new RTK preset {preset.title!r} for serial port")

        current_preset = self.current_preset
        if current_preset and current_preset.id not in self._registry:
            self._request_preset_switch_later(None)

    @staticmethod
    def _get_dynamic_preset_id_for_serial_port(port, index: int = 0) -> str:
        from flockwave.spec.ids import make_valid_object_id

        return make_valid_object_id(f"{port.device}/{index}")


construct = RTKExtension
dependencies = ("ntrip", "signals")
optional_dependencies = {
    "hotplug": "detects when new USB devices are plugged in and updates the RTK sources automatically"
}
