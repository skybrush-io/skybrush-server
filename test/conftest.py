"""Shared pytest fixtures for the unit tests."""

from pytest import fixture

from .recording_message_hub import RecordingMessageHub


@fixture
def hub() -> RecordingMessageHub:
    """Message hub that records outbound messages instead of sending them."""
    return RecordingMessageHub()
