from pytest import raises

from flockwave.server.ext.beacon.model import Beacon
from flockwave.server.model import UAV, PassiveUAVDriver, UAVBase
from flockwave.server.registries.errors import RegistryFull
from flockwave.server.registries.objects import ObjectRegistry


def create_uav(id: str) -> UAVBase:
    return UAVBase(id, PassiveUAVDriver())


def test_uav_limit_applies_to_uavs_only():
    registry = ObjectRegistry()
    registry.uav_limit = 1

    registry.add(Beacon("beacon-1"))
    registry.add(create_uav("uav-1"))
    registry.add(Beacon("beacon-2"))
    assert registry.uav_limit_reached

    with raises(RegistryFull):
        registry.add(create_uav("uav-2"))

    registry.remove_by_id("uav-1")
    assert not registry.uav_limit_reached
    registry.add(create_uav("uav-2"))


def test_ids_by_type_uav():
    registry = ObjectRegistry()
    registry.add(create_uav("uav-1"))
    registry.add(Beacon("beacon-1"))
    registry.add(create_uav("uav-2"))
    registry.remove_by_id("uav-1")
    registry.add(create_uav("uav-3"))

    assert list(registry.ids_by_type(UAV)) == ["uav-2", "uav-3"]


def test_add_registered_object_is_no_op():
    registry = ObjectRegistry()
    uav = create_uav("uav-1")
    registry.add(uav)
    registry.uav_limit = 1
    added = []

    with registry.added.connected_to(lambda _, object: added.append(object)):
        registry.add(uav)

    assert added == []
    assert list(registry.ids_by_type(UAV)) == ["uav-1"]
