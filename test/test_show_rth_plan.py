from pytest import fixture, raises

from flockwave.server.show.rth_plan import RTHAction, RTHPlan, RTHPlanEntry


@fixture
def plan() -> RTHPlan:
    plan = RTHPlan()

    entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=5,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(20, 20),
        duration=10,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(
        time=25,
        action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
        target=(-7, 35),
        duration=15,
    )
    plan.add_entry(entry)

    entry = RTHPlanEntry(time=40, action=RTHAction.LAND)
    plan.add_entry(entry)

    return plan


class TestRTHPlanEntry:
    def test_rth_plan_entry_has_target(self):
        entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
        assert not entry.has_target

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
        )
        assert entry.has_target

    def test_rth_plan_entry_has_pre_delay(self):
        entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
        assert not entry.has_pre_delay

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
            pre_delay=5,
        )
        assert entry.has_pre_delay

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
            pre_delay=-2,
        )
        assert not entry.has_pre_delay

    def test_rth_plan_entry_has_post_delay(self):
        entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
        assert not entry.has_post_delay

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
            post_delay=5,
        )
        assert entry.has_post_delay

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
            post_delay=-2,
        )
        assert not entry.has_post_delay

    def test_rth_plan_json_conversion(self):
        entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
        assert entry.to_json() == {"time": 0, "action": "land"}
        restored_entry = RTHPlanEntry.from_json(entry.to_json())
        assert entry == restored_entry

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
            pre_delay=23,
            post_delay=3,
        )
        assert entry.to_json() == {
            "time": 5,
            "action": "goTo",
            "target": (20, 20),
            "duration": 10,
            "preDelay": 23,
            "postDelay": 3,
        }
        restored_entry = RTHPlanEntry.from_json(entry.to_json())
        assert entry == restored_entry

    def test_rth_plan_entry_invalid_json(self):
        invalids = [
            {},
            {"time": -2},
            {"time": 2.5},
            {"time": 5, "action": "goTo", "target": ("aaa", 47)},
            {"time": 5, "action": "goTo", "target": (20, 20)},
            {"time": 5.0, "action": "goTo", "target": (20, 20), "duration": 3.1415},
            {
                "time": 5.0,
                "action": "goTo",
                "target": (20, 20),
                "duration": 3.0,
                "preDelay": "aaa",
            },
            {
                "time": 5.0,
                "action": "goTo",
                "target": (20, 20),
                "duration": 3.0,
                "preDelay": 5.0,
                "postDelay": "spam",
            },
        ]

        for item in invalids:
            with raises(ValueError):
                RTHPlanEntry.from_json(item)


class TestRTHPlan:
    def test_rth_plan_adding_items(self):
        plan = RTHPlan()

        assert plan.is_empty
        assert len(plan) == 0

        entry = RTHPlanEntry(time=0, action=RTHAction.LAND)
        plan.add_entry(entry)
        assert not plan.is_empty
        assert len(plan) == 1
        assert plan[0] is entry

        next_entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
        )
        plan.add_entry(next_entry)
        assert not plan.is_empty
        assert list(plan) == [entry, next_entry]

        with raises(RuntimeError, match="must be larger"):
            plan.add_entry(entry)
        with raises(RuntimeError, match="must be larger"):
            plan.add_entry(next_entry)

        plan.clear()
        assert plan.is_empty
        assert len(plan) == 0

    def test_rth_plan_get_padded_bounding_box(self, plan: RTHPlan):
        assert plan.get_padded_bounding_box(5) == ((-12, 15), (25, 40))

        plan.clear()
        with raises(ValueError):
            plan.get_padded_bounding_box(5)

    def test_rth_plan_bounding_box(self, plan: RTHPlan):
        assert plan.bounding_box == ((-7, 20), (20, 35))

        plan.clear()
        with raises(ValueError):
            _ = plan.bounding_box

    def test_rth_plan_propose_scaling_factor(self, plan: RTHPlan):
        # Scaling factor will be 2 because one of the points has a target > 32.5m
        assert plan.propose_scaling_factor() == 2

        plan.clear()
        assert plan.propose_scaling_factor() == 1

        entry = RTHPlanEntry(
            time=5,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(20, 20),
            duration=10,
        )
        plan.add_entry(entry)

        # Now everything is within +-32.5 m so a scaling factor of 1 is OK
        assert plan.propose_scaling_factor() == 1

        entry = RTHPlanEntry(
            time=15,
            action=RTHAction.GO_TO_KEEPING_ALTITUDE_AND_LAND,
            target=(-3270, 20),
            duration=10,
        )
        plan.add_entry(entry)

        # Now we need a scaling factor of 10 because one target is at -3270m
        assert plan.propose_scaling_factor() == 100

    def test_rth_plan_json_conversion(self, plan: RTHPlan):
        encoded_plan = plan.to_json()
        decoded_plan = RTHPlan.from_json(encoded_plan)
        assert list(decoded_plan) == list(plan)

    def test_rth_plan_invalid_json(self):
        invalids = [{}, {"version": 1}]

        for item in invalids:
            with raises(RuntimeError):
                RTHPlan.from_json(item)
