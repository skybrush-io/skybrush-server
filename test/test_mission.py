from pytest import fixture, raises

from flockwave.server.model.mission import (
    Altitude,
    AltitudeReference,
    GoToMissionCommand,
    LandMissionCommand,
    MissionCommand,
    MissionCommandBundle,
    ReturnToHomeMissionCommand,
    TakeoffMissionCommand,
)


@fixture
def command() -> MissionCommand:
    return GoToMissionCommand(
        id=None,
        participants=None,
        latitude=47,
        longitude=19,
        altitude=Altitude(value=100, reference=AltitudeReference.HOME),
    )


@fixture
def bundle() -> MissionCommandBundle:
    return MissionCommandBundle(
        commands=[
            TakeoffMissionCommand(
                id=None,
                participants=None,
                altitude=Altitude(value=10, reference=AltitudeReference.HOME),
            ),
            GoToMissionCommand(
                id=None,
                participants=[0],
                latitude=47.1,
                longitude=19.1,
                altitude=Altitude(value=100, reference=AltitudeReference.HOME),
            ),
            GoToMissionCommand(
                id=None,
                participants=[1],
                latitude=47.2,
                longitude=19.2,
                altitude=Altitude(value=100, reference=AltitudeReference.HOME),
            ),
            ReturnToHomeMissionCommand(id=None, participants=None),
            LandMissionCommand(id=None, participants=None),
        ],
        start_positions=[
            (470000000, 190000000),
            (470010000, 190000000),
            (470020000, 190000000),
        ],
    )


def test_mission_command(command):
    assert command == GoToMissionCommand.from_json(command.json)


def test_participants(command):
    command.participants = [0, 1, 2]
    assert command == GoToMissionCommand.from_json(command.json)


def test_invalid_participants(command):
    command.participants = [0, 1, -1]
    with raises(
        RuntimeError, match="mission item participant IDs must be nonnegative integers"
    ):
        command = GoToMissionCommand.from_json(command.json)


def test_mission_command_bundle(bundle):
    assert bundle.participants == [0, 1]
    assert bundle == MissionCommandBundle.from_json(bundle.json)
