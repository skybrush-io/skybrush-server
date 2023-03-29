from flockwave.server.model.attitude import Attitude
from flockwave.server.model.gps import GPSFix, GPSFixType
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


def test_gpsfix():
    gps = GPSFix(GPSFixType.FIX_3D, 15, 1.2, 1.5)
    assert gps.json == [3, 15, 1200, 1500]

    gps = GPSFix(GPSFixType.RTK_FLOAT, num_satellites=15)
    assert gps.json == [5, 15]

    gps = GPSFix(GPSFixType.RTK_FIXED, horizontal_accuracy=1.5)
    assert gps.json == [6, None, 1500]

    gps.update_from(GPSFix(GPSFixType.STATIC, vertical_accuracy=2))
    assert gps.json == [7, None, None, 2000]

    gps.update_from(GPSFixType.DGPS)
    assert gps.json == [4]


def test_uavstatusinfo():
    status = UAVStatusInfo()

    assert status.attitude is None
