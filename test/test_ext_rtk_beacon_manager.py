from unittest.mock import Mock

from pytest import mark

from flockwave.server.ext.rtk.beacon_manager import RTKBeaconManager


@mark.parametrize("enabled, app", [(False, Mock()), (True, None)])
def test_use_without_beacon_is_noop_context(enabled: bool, app: Mock | None):
    manager = RTKBeaconManager()
    manager.enabled = enabled

    with manager.use(Mock(app=app), nursery=Mock()):
        pass
