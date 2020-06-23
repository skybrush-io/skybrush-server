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

from ..base import ExtensionBase

from .preset import RTKConfigurationPreset
from .registry import RTKPresetRegistry


class RTKExtension(ExtensionBase):
    """Extension that connects to one or more data sources for RTK connections
    and forwards the corrections to the UAVs managed by the server.
    """

    def __init__(self):
        """Constructor."""
        super().__init__()

        self._current_preset = None
        self._presets = []
        self._registry = None
        self._rtk_preset_task_cancel_scope = None
        self._command_queue = None

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "rtk:{0}")
        self._presets = []
        for id, spec in configuration.get("presets", {}).items():
            try:
                self._presets.append(RTKConfigurationPreset.from_json(spec, id=id))
            except Exception:
                self.log.exception(f"Ignoring invalid RTK configuration {id!r}")

    @property
    def current_preset(self) -> RTKConfigurationPreset:
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
            Optional[Clock]: the clock with the given ID or ``None`` if there
                is no such clock
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

    def handle_RTK_SELECT(self, message: FlockwaveMessage, sender, hub: MessageHub):
        """Handles an incoming RTK-SELECT message."""
        if "id" in message.body:
            # Selecting a new RTK source to use
            if message.body["id"] is None:
                desired_preset = None
            else:
                desired_preset = self.find_preset_by_id(message.body["id"])
                if desired_preset is None:
                    return hub.reject(message, reason="No such RTK preset")

            self.app.run_in_background(self._request_preset_switch, desired_preset)

            return hub.acknowledge(message)
        else:
            # Querying the currently used RTK source
            return hub.create_response_or_notification(
                body={"id": self.current_preset.id if self.current_preset else None},
                in_response_to=message,
            )

    async def run(self, app, configuration, logger):
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

            for preset in self._presets:
                self._registry.add(preset)

            stack.enter_context(
                app.message_hub.use_message_handlers(
                    {
                        "X-RTK-INF": self.handle_RTK_INF,
                        "X-RTK-LIST": self.handle_RTK_LIST,
                        "X-RTK-SELECT": self.handle_RTK_SELECT,
                    }
                )
            )

            async with self.use_nursery():
                async for message, args in rx_queue:
                    if message == "set_preset":
                        await self._perform_preset_switch(args)

    async def _request_preset_switch(self, value: RTKConfigurationPreset) -> None:
        """Requests the extension to switch to a new RTK preset."""
        if not self._tx_queue:
            self.log.warning("Cannot set RTK preset when the extension is not running")
        else:
            await self._tx_queue.send(("set_preset", value))

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
            async with open_nursery() as nursery:
                task_status.started(nursery.cancel_scope)

                for source in preset.sources:
                    try:
                        connection = create_connection(source)
                        stack.enter_context(
                            self.app.connection_registry.use(
                                connection,
                                self._id_format.format(preset.id),
                                preset.title,
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
                if preset.accepts(packet):
                    encoded = encoder(packet)
                    signal.send(packet=encoded)


construct = RTKExtension
dependencies = ("ntrip", "signals")
