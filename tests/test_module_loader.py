import pytest
from builder.module_loader import Module, load_all_modules


class TestStageField:
    def test_stage_defaults_to_preapplied(self):
        m = Module(
            id="v1", name="v1", description="", type="vulnerability",
            difficulty="easy", points=100, category="general",
        )
        assert m.stage == "preapplied"

    def test_stage_can_be_caldera(self):
        m = Module(
            id="v1", name="v1", description="", type="vulnerability",
            difficulty="easy", points=100, category="general",
            stage="caldera",
        )
        assert m.stage == "caldera"

    def test_hardening_stage_is_none(self):
        m = Module(
            id="h1", name="h1", description="", type="hardening",
            difficulty="easy", points=100, category="general",
        )
        assert m.stage is None


class TestGoalFields:
    def test_goal_has_red_points(self):
        m = Module(
            id="g1", name="g1", description="", type="goal",
            difficulty="medium", points=0, category="impact",
            red_points=300, defend_points=200,
            revert_verification={"type": "http_response", "label": "test"},
        )
        assert m.red_points == 300
        assert m.defend_points == 200
        assert m.revert_verification == {"type": "http_response", "label": "test"}

    def test_goal_defaults_zero_points(self):
        m = Module(
            id="g1", name="g1", description="", type="goal",
            difficulty="easy", points=0, category="impact",
        )
        assert m.red_points == 0
        assert m.defend_points == 0
        assert m.revert_verification == {}


@pytest.mark.skip(reason="goal YAMLs not yet created — created in Task 2")
class TestLoadGoalModule:
    def test_goal_modules_loaded(self):
        """Verifies goal modules from modules/goals/ are picked up by load_all_modules()."""
        modules = load_all_modules()
        goals = [m for m in modules if m.type == "goal"]
        assert len(goals) >= 1, "Expected at least one goal module to be loaded"

    def test_goal_stage_is_none(self):
        modules = load_all_modules()
        goals = [m for m in modules if m.type == "goal"]
        for g in goals:
            assert g.stage is None, f"Goal {g.id} should have stage=None"
