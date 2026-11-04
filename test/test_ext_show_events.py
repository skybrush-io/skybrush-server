"""Tests for the events dispatched by the `show` extension."""

from collections.abc import Iterator
from contextlib import ExitStack
from logging import getLogger
from unittest.mock import MagicMock

from pytest import fixture

from flockwave.server.ext.show.extension import DroneShowExtension


@fixture
def config_events() -> MagicMock:
    return MagicMock()


@fixture
def lights_events() -> MagicMock:
    return MagicMock()


@fixture
def ext(
    config_events: MagicMock, lights_events: MagicMock
) -> Iterator[DroneShowExtension]:
    ext = DroneShowExtension()
    ext.app = MagicMock()
    ext.log = getLogger(__name__)
    ext._config_events = config_events
    ext._lights_events = lights_events

    with ExitStack() as stack:
        stack.enter_context(
            ext._config.updated_v2.connected_to(
                ext._on_config_updated, sender=ext._config
            )
        )
        stack.enter_context(
            ext._lights.updated.connected_to(ext._on_lights_updated, sender=ext._lights)
        )
        yield ext


def test_config_change_dispatches_event(
    ext: DroneShowExtension, config_events: MagicMock, lights_events: MagicMock
):
    ext._config.update_from_json(
        {"start": {"authorized": True, "authorizationScope": "live"}}
    )
    config_events.dispatch.assert_called_once_with()
    lights_events.dispatch.assert_not_called()


def test_noop_config_update_dispatches_nothing(
    ext: DroneShowExtension, config_events: MagicMock
):
    ext._config.update_from_json({"start": {"authorized": False}})
    config_events.dispatch.assert_not_called()


def test_light_change_dispatches_event(
    ext: DroneShowExtension, config_events: MagicMock, lights_events: MagicMock
):
    ext._lights.update_from_json({"color": [255, 0, 0], "effect": "solid"})
    lights_events.dispatch.assert_called_once_with()
    config_events.dispatch.assert_not_called()
