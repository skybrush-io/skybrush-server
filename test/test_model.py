from flockwave.server.model.attitude import Attitude
from flockwave.server.model.uav import UAVStatusInfo


def test_attitude():
    attitude = Attitude()
    attitude.roll = 10
    attitude.pitch = 20
    attitude.yaw = 30

    assert attitude.json == [100, 200, 300]

    attitude.update_from(Attitude(200, 210, 220))

    assert attitude.json == [-1600, -1500, 2200]
    assert attitude.json == Attitude.from_json(attitude.json).json


def test_uavstatusinfo():
    status = UAVStatusInfo()

    assert status.attitude == None
