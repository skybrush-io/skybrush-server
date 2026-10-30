from time import monotonic

from flockwave.gps.vectors import (
    FlatEarthToGPSCoordinateTransformation,
    GPSCoordinate,
    Vector3D,
)

from flockwave.server.ext.virtual_uavs.driver import (
    VirtualUAV,
    VirtualUAVDriver,
    VirtualUAVState,
)
from flockwave.server.show.player import TrajectoryPlayer
from flockwave.server.show.trajectory import TrajectorySpecification


def create_airborne_uav_with_ended_trajectory(z: float) -> VirtualUAV:
    driver = VirtualUAVDriver()
    uav = driver.create_uav("01", home=GPSCoordinate(lat=0, lon=0))

    uav.state = VirtualUAVState.AIRBORNE
    uav._position_xyz = Vector3D(x=0, y=0, z=z)
    uav.target_xyz = (0, 0, z)

    player = TrajectoryPlayer(TrajectorySpecification({"version": 1, "points": []}))
    player._select_segment(2)
    assert player.ended

    uav._trajectory_player = player
    uav._trajectory_transformation = FlatEarthToGPSCoordinateTransformation(
        origin=GPSCoordinate(lat=0, lon=0)
    )
    uav._mission_started_at = monotonic()

    return uav


def test_virtual_uav_lands_when_trajectory_ends_at_ground_level():
    uav = create_airborne_uav_with_ended_trajectory(z=0.05)

    uav.step(0.1)

    assert uav.state is VirtualUAVState.LANDED
    assert uav.target is None


def test_virtual_uav_lands_when_trajectory_ends_above_ground():
    uav = create_airborne_uav_with_ended_trajectory(z=6)

    for _ in range(200):
        uav.step(0.1)
        if uav.state is VirtualUAVState.LANDED:
            break

    assert uav.state is VirtualUAVState.LANDED
    assert uav._position_xyz.z < 0.1
