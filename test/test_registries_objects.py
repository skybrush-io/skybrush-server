from pytest import raises

from flockwave.server.ext.beacon.model import Beacon
from flockwave.server.model import UAV, PassiveUAVDriver, UAVBase
from flockwave.server.registries.errors import RegistryFull
from flockwave.server.registries.objects import ObjectRegistry


def create_uav(id: str) -> UAVBase:
    return UAVBase(id, PassiveUAVDriver())


def create_registry() -> ObjectRegistry:
    return ObjectRegistry(counts_towards_limit=lambda obj: isinstance(obj, UAV))


def test_size_limit_applies_to_matching_objects_only():
    registry = create_registry()
    registry.size_limit = 1

    registry.add(Beacon("beacon-1"))
    registry.add(create_uav("uav-1"))
    registry.add(Beacon("beacon-2"))
    assert registry.size_limit_reached

    with raises(RegistryFull):
        registry.add(create_uav("uav-2"))

    registry.remove_by_id("uav-1")
    assert not registry.size_limit_reached
    registry.add(create_uav("uav-2"))


def test_size_limit_applies_to_all_objects_by_default():
    registry = ObjectRegistry()
    registry.size_limit = 1
    registry.add(Beacon("beacon-1"))

    with raises(RegistryFull):
        registry.add(create_uav("uav-1"))


def test_size_limit_set_after_objects_were_added():
    registry = create_registry()
    registry.add(create_uav("uav-1"))
    registry.size_limit = 1

    with raises(RegistryFull):
        registry.add(create_uav("uav-2"))


def test_add_registered_object_is_no_op():
    registry = create_registry()
    uav = create_uav("uav-1")
    registry.add(uav)
    registry.size_limit = 1
    added = []

    with registry.added.connected_to(lambda _, object: added.append(object)):
        registry.add(uav)

    assert added == []
