from pathlib import Path

import yaml

from builder.base_loader import CopyStep, PlaybookStep, RunStep, load_all_bases
from builder.module_loader import Module, load_all_modules


REPO_ROOT = Path(__file__).resolve().parent.parent


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


class TestPhasesAndNarrative:
    def test_phases_and_narrative_default_empty(self):
        m = Module(
            id="x", name="X", description="", type="vulnerability",
            difficulty="easy", points=0, category="test",
        )
        assert m.phases == []
        assert m.narrative == ""

    def test_phases_and_narrative_load_from_yaml(self, tmp_path, monkeypatch):
        import builder.module_loader as ml
        (tmp_path / "m.yaml").write_text(
            "id: sample\nname: Sample\ndescription: d\ntype: vulnerability\n"
            "difficulty: easy\npoints: 10\ncategory: web\n"
            "phases: [recon, impact]\nnarrative: An attacker pivots through the web tier.\n"
        )
        monkeypatch.setattr(ml, "MODULES_DIR", tmp_path)
        modules = ml.load_all_modules()
        sample = next(m for m in modules if m.id == "sample")
        assert sample.phases == ["recon", "impact"]
        assert sample.narrative == "An attacker pivots through the web tier."


class TestRepositoryDefinitions:
    def test_every_yaml_definition_parses(self):
        definitions = [
            *sorted((REPO_ROOT / "modules").rglob("*.yaml")),
            *sorted((REPO_ROOT / "bases").rglob("*.yaml")),
        ]
        assert definitions
        for path in definitions:
            with path.open(encoding="utf-8") as stream:
                parsed = yaml.safe_load(stream)
            assert isinstance(parsed, dict), f"{path} must contain a YAML mapping"

    def test_module_ids_are_unique_and_dependencies_exist(self):
        modules = load_all_modules()
        ids = [module.id for module in modules]
        assert len(ids) == len(set(ids)), "Module IDs must be globally unique"

        known_ids = set(ids)
        for module in modules:
            missing = set(module.requires) - known_ids
            assert not missing, f"{module.id} requires missing modules: {sorted(missing)}"
            unknown_conflicts = set(module.conflicts) - known_ids
            assert not unknown_conflicts, (
                f"{module.id} conflicts with missing modules: {sorted(unknown_conflicts)}"
            )

    def test_module_step_sources_exist(self):
        for module in load_all_modules():
            for step in module.steps:
                source = module.source_dir / (
                    step.script if isinstance(step, RunStep) else step.src
                )
                assert source.exists(), f"{module.id} references missing source {source}"

    def test_base_ids_are_unique_and_step_sources_exist(self):
        bases = load_all_bases()
        ids = [base.id for base in bases]
        assert len(ids) == len(set(ids)), "Base IDs must be globally unique"
        for base in bases:
            for step in base.steps:
                if isinstance(step, (RunStep, PlaybookStep)):
                    relative = step.script if isinstance(step, RunStep) else step.playbook
                elif isinstance(step, CopyStep):
                    relative = step.src
                else:
                    raise AssertionError(f"Unsupported base step: {step!r}")
                source = base.source_dir / relative
                assert source.exists(), f"{base.id} references missing source {source}"

    def test_all_modules_declare_supported_bases(self):
        bases = {base.id for base in load_all_bases()}
        for module in load_all_modules():
            assert module.supported_bases, f"{module.id} must declare supported_bases"
            unknown = set(module.supported_bases) - bases
            assert not unknown, f"{module.id} declares unknown base types: {sorted(unknown)}"

    def test_unmapped_tactics_declare_phase_override(self):
        from builder.attack_tree import TACTIC_PHASE
        for module in load_all_modules():
            cal = module.caldera or {}
            tactic = cal.get("tactic")
            if not tactic or module.type == "goal":
                continue
            assert tactic in TACTIC_PHASE or "phase_override" in cal, (
                f"{module.id} uses unmapped tactic '{tactic}' and must declare phase_override"
            )

    def test_known_phase_overrides_resolve(self):
        from builder.attack_tree import build_attack_tree
        tree = build_attack_tree(load_all_modules())
        assert tree.nodes["monitord_writable_logdir"].phase == 3
        assert tree.nodes["ip_forwarding_enabled"].phase == 4
