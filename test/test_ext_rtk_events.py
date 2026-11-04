"""Tests for the events dispatched by the `rtk` extension."""

from logging import getLogger
from types import SimpleNamespace
from unittest.mock import MagicMock

from flockwave.gps.vectors import ECEFCoordinate
from pytest import fixture

from flockwave.server.ext.rtk.enums import RTKConfigurationPresetType
from flockwave.server.ext.rtk.extension import RTKExtension
from flockwave.server.ext.rtk.preset import RTKConfigurationPreset
from flockwave.server.ext.rtk.registry import RTKPresetRegistry
from flockwave.server.message_hub import MessageHub


@fixture
def hub() -> MessageHub:
    return MessageHub()


@fixture
def events() -> SimpleNamespace:
    """Mock dispatchers for the events of the extension."""
    return SimpleNamespace(presets=MagicMock(), source=MagicMock(), survey=MagicMock())


@fixture
def ext(events: SimpleNamespace) -> RTKExtension:
    ext = RTKExtension()
    ext.app = MagicMock()
    ext.log = getLogger(__name__)
    ext._registry = RTKPresetRegistry()
    ext._presets_events = events.presets
    ext._source_events = events.source
    ext._survey_events = events.survey
    return ext


def create_user_preset(ext: RTKExtension, id: str = "user") -> RTKConfigurationPreset:
    preset = RTKConfigurationPreset.from_json(
        {"title": "User preset"}, id=id, type=RTKConfigurationPresetType.USER
    )
    assert ext._registry is not None
    ext._registry.add(preset)
    return preset


def test_new_preset_dispatches_presets_event(
    ext: RTKExtension, events: SimpleNamespace, hub: MessageHub
):
    message = hub._message_builder.create_message(
        {"type": "X-RTK-NEW", "preset": {"title": "New preset"}}
    )
    response = ext.handle_RTK_NEW(message, None, hub)

    assert "id" in response.body
    events.presets.dispatch.assert_called_once_with()


def test_rejected_new_preset_dispatches_nothing(
    ext: RTKExtension, events: SimpleNamespace, hub: MessageHub
):
    message = hub._message_builder.create_message(
        {"type": "X-RTK-NEW", "preset": {"id": "foo", "title": "New preset"}}
    )
    ext.handle_RTK_NEW(message, None, hub)

    events.presets.dispatch.assert_not_called()


def test_updating_and_deleting_presets_dispatches_presets_event(
    ext: RTKExtension, events: SimpleNamespace
):
    preset = create_user_preset(ext)

    ext.update_preset(preset, {"title": "Renamed"})
    assert events.presets.dispatch.call_count == 1

    ext.delete_preset(preset)
    assert events.presets.dispatch.call_count == 2


def test_survey_settings_update_dispatches_survey_event(
    ext: RTKExtension, events: SimpleNamespace, hub: MessageHub
):

    message = hub._message_builder.create_message(
        {"type": "RTK-SURVEY", "settings": {"duration": 30}}
    )
    ext.handle_RTK_SURVEY(message, None, hub)
    events.survey.dispatch.assert_called_once_with()

    # Invalid settings do not dispatch an event
    message = hub._message_builder.create_message(
        {"type": "RTK-SURVEY", "settings": "invalid"}
    )
    ext.handle_RTK_SURVEY(message, None, hub)
    assert events.survey.dispatch.call_count == 1

    # Querying the settings does not dispatch an event
    message = hub._message_builder.create_message({"type": "RTK-SURVEY"})
    ext.handle_RTK_SURVEY(message, None, hub)
    assert events.survey.dispatch.call_count == 1


def test_source_selection_resetting_position_dispatches_survey_event(
    ext: RTKExtension, events: SimpleNamespace, hub: MessageHub
):
    ext._survey_settings.position = ECEFCoordinate(1, 2, 3)

    message = hub._message_builder.create_message({"type": "RTK-SOURCE", "id": None})
    ext.handle_RTK_SOURCE(message, None, hub)
    assert ext._survey_settings.position is None
    events.survey.dispatch.assert_called_once_with()

    # Position is already reset, no new event
    ext.handle_RTK_SOURCE(message, None, hub)
    assert events.survey.dispatch.call_count == 1


async def test_source_switch_dispatches_source_event(
    ext: RTKExtension, events: SimpleNamespace
):
    ext._nursery = MagicMock()
    ext._current_preset = create_user_preset(ext)

    await ext._perform_preset_switch(None)
    assert ext.current_preset is None
    events.source.dispatch.assert_called_once_with()

    # Switching to the same preset is a no-op
    await ext._perform_preset_switch(None)
    assert events.source.dispatch.call_count == 1


def test_dynamic_preset_changes_dispatch_presets_event(
    ext: RTKExtension, events: SimpleNamespace, monkeypatch
):
    monkeypatch.setattr(
        "flockwave.server.ext.rtk.extension.list_serial_ports", lambda: []
    )
    assert ext._registry is not None

    # No serial ports and no dynamic presets, nothing changes
    ext._update_dynamic_presets()
    events.presets.dispatch.assert_not_called()

    # A dynamic preset whose serial port is gone gets removed
    ext._registry.add(
        RTKConfigurationPreset.from_json(
            {"title": "Unplugged"}, id="dyn", type=RTKConfigurationPresetType.DYNAMIC
        )
    )
    ext._update_dynamic_presets()
    assert "dyn" not in ext._registry
    events.presets.dispatch.assert_called_once_with()
