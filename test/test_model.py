import json

from flockwave.encoders.json import create_json_encoder

from flockwave.server.model.attitude import Attitude
from flockwave.server.model.gps import GPSFix, GPSFixType
from flockwave.server.model.uav import UAVBase, UAVStatusInfo
from flockwave.server.model.wind import WindEstimate


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


def test_wind_estimate():
    wind = WindEstimate()
    assert wind.json == [0, 0]

    wind.speed = 5.4
    wind.direction = 90.0
    assert wind.json == [5400, 900]

    # Directions are wrapped to [0, 360) and negative speeds are clamped
    wind.update_from(WindEstimate(speed=-1, direction=359.99))
    assert wind.json == [0, 0]

    wind = WindEstimate(speed=0.2, direction=1.40625)
    assert wind.json == [200, 14]
    assert wind.json == WindEstimate.from_json(wind.json).json


def test_uav_update_status_wind():
    class DummyUAV(UAVBase):
        pass

    uav = DummyUAV("1", None)  # type: ignore
    assert uav.status.wind is None

    estimate = WindEstimate(speed=5.4, direction=90)
    uav.update_status(wind=estimate)
    assert uav.status.wind is not None
    assert uav.status.wind.json == [5400, 900]

    # The estimate is cloned
    estimate.speed = 10
    assert uav.status.wind.json == [5400, 900]

    # The encoded status contains the estimate and conforms to the schema.
    # Unset optional fields are encoded as null, and the schema does not allow
    # null values, so they are removed before the validation.
    encoded = json.loads(create_json_encoder()(uav.status.json))
    assert encoded["wind"] == [5400, 900]
    UAVStatusInfo.from_json({k: v for k, v in encoded.items() if v is not None})

    # None leaves the estimate unchanged, False clears it
    uav.update_status(wind=None)
    assert uav.status.wind.json == [5400, 900]
    uav.update_status(wind=False)
    assert uav.status.wind is None
