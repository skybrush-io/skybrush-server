from flockwave.server.ext.show.config import (
    AuthorizationScope,
    DroneShowConfiguration,
    StartMethod,
)


def test_show_config_defaults():
    config = DroneShowConfiguration()

    assert config.authorized_to_start is False
    assert config.authorization_scope is AuthorizationScope.NONE
    assert config.scope_iff_authorized is AuthorizationScope.NONE
    assert config.clock is None
    assert config.start_time_on_clock is None
    assert config.start_method is StartMethod.RC
    assert config.uav_ids == []


def test_show_config_update_from_json_without_scope():
    # Interactions in this unit test should model how Skybrush Live used to
    # behave before the authorization scope was introduced

    obj = {
        "start": {
            "authorized": True,
            "clock": "mtc",
            "time": 123.456,
            "method": "rc",
            "uavIds": ["test"],
        }
    }

    config = DroneShowConfiguration()
    config.update_from_json(obj)

    assert config.authorized_to_start is True
    assert config.authorization_scope is AuthorizationScope.LIVE
    assert config.scope_iff_authorized is AuthorizationScope.LIVE
    assert config.clock == "mtc"
    assert config.start_time_on_clock == 123.456
    assert config.start_method is StartMethod.RC
    assert config.uav_ids == ["test"]

    obj["start"]["authorizationScope"] = "live"
    assert config.json == obj
    del obj["start"]["authorizationScope"]

    obj["start"]["authorized"] = False

    config = DroneShowConfiguration()
    config.update_from_json(obj)

    assert config.authorized_to_start is False
    assert config.authorization_scope is AuthorizationScope.NONE
    assert config.scope_iff_authorized is AuthorizationScope.NONE


def test_show_config_update_from_json_with_scope():
    # Interactions in this unit test should model how Skybrush Live behaves
    # after introducing authorization scopes on the UI

    obj = {
        "start": {
            "authorized": True,
            "authorizationScope": "live",
            "clock": "mtc",
            "time": 123.456,
            "method": "rc",
            "uavIds": ["test"],
        }
    }

    config = DroneShowConfiguration()
    config.update_from_json(obj)

    assert config.authorized_to_start is True
    assert config.authorization_scope is AuthorizationScope.LIVE
    assert config.scope_iff_authorized is AuthorizationScope.LIVE
    assert config.clock == "mtc"
    assert config.start_time_on_clock == 123.456
    assert config.start_method is StartMethod.RC
    assert config.uav_ids == ["test"]

    assert config.json == obj

    obj["start"]["authorized"] = False
    del obj["start"]["authorizationScope"]

    config.update_from_json(obj)

    assert config.authorized_to_start is False
    assert config.authorization_scope is AuthorizationScope.LIVE  # can stay as is
    assert (
        config.scope_iff_authorized is AuthorizationScope.NONE
    )  # must reflect that we have no authorization


def test_show_config_partial_update_from_json():
    config = DroneShowConfiguration()
    config.update_from_json({"start": {"authorized": True}})

    assert config.authorized_to_start is True
    assert config.authorization_scope is AuthorizationScope.LIVE
    assert config.scope_iff_authorized is AuthorizationScope.LIVE
    assert config.clock is None
    assert config.start_time_on_clock is None
    assert config.start_method is StartMethod.RC

    config.update_from_json({"start": {"method": "auto"}})

    assert config.authorized_to_start is True
    assert config.authorization_scope is AuthorizationScope.LIVE
    assert config.scope_iff_authorized is AuthorizationScope.LIVE
    assert config.start_method is StartMethod.AUTO

    config.update_from_json({"start": {"authorizationScope": "lights"}})

    assert config.authorized_to_start is True
    assert config.authorization_scope is AuthorizationScope.LIGHTS_ONLY
    assert config.scope_iff_authorized is AuthorizationScope.LIGHTS_ONLY
    assert config.start_method is StartMethod.AUTO

    config.update_from_json({"start": {"authorized": False}})

    assert config.authorized_to_start is False
    assert config.authorization_scope is AuthorizationScope.LIGHTS_ONLY
    assert config.scope_iff_authorized is AuthorizationScope.NONE
    assert config.start_method is StartMethod.AUTO
