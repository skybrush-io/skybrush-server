"""Shared pytest fixtures for the unit tests."""

from pytest import fixture

from .recording_message_hub import RecordingMessageHub


class FakeClock:
    """Clock that returns a manually adjustable timestamp, in seconds."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@fixture
def clock() -> FakeClock:
    """Fake clock that starts at zero and moves only when its ``now``
    attribute is set.
    """
    return FakeClock()


@fixture
def hub() -> RecordingMessageHub:
    """Message hub that records outbound messages instead of sending them."""
    return RecordingMessageHub()
