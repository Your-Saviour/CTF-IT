"""Tests for builder.caldera plugin generation (abilities, uploads, adversaries)."""

import tempfile
from pathlib import Path

import pytest

from builder.caldera import (
    PLUGIN_NAME,
    RECON_MARKER,
    _build_abilities,
    _default_exploit_requirement,
    _default_recon_parsers,
    _gate_exploit_command,
    _render_ability,
    build_path_adversaries,
    fact_trait,
    generate_caldera_export,
    generate_caldera_export_multi_path,
    generate_caldera_event_export,
)
from builder.module_loader import Module


def _mod(
    id,
    name=None,
    type="vulnerability",
    difficulty="easy",
    category="general",
    requires=None,
    caldera=None,
    source_dir=None,
):
    return Module(
        id=id,
        name=name or id,
        description="",
        type=type,
        difficulty=difficulty,
        points=100,
        category=category,
        requires=requires or [],
        caldera=caldera,
        source_dir=source_dir or Path("."),
    )


def _caldera(tactic, attack_id="T0000", technique_name="Test", recon_cmd="echo recon", exploit_cmd="echo exploit"):
    return {
        "tactic": tactic,
        "technique": {"attack_id": attack_id, "name": technique_name},
        "recon": {"description": "recon", "command": recon_cmd},
        "exploit": {"description": "exploit", "command": exploit_cmd},
    }


# ── Ability build & uploads ──

class TestAbilityUploads:
    def test_exploit_uploads_preserved_in_ability_dict(self):
        """Exploit uploads are prefixed with the module id in the ability dict."""
        m = _mod("v1", caldera=_caldera(
            "initial-access", exploit_cmd="cp ./shell.php /tmp/x.php",
        ))
        m.caldera["exploit"]["uploads"] = ["shell.php"]
        abilities = _build_abilities([m])
        exploit = [a for a in abilities if a["phase"] == "exploit"][0]
        assert exploit["uploads"] == ["v1__shell.php"]

    def test_exploit_uploads_substituted_into_command(self):
        """Command references to upload files are rewritten to prefixed names."""
        m = _mod("v1", caldera=_caldera(
            "initial-access", exploit_cmd="./shell.php && cat shell.php",
        ))
        m.caldera["exploit"]["uploads"] = ["shell.php"]
        abilities = _build_abilities([m])
        exploit = [a for a in abilities if a["phase"] == "exploit"][0]
        assert "./v1__shell.php" in exploit["command"]
        assert "v1__shell.php && cat" in exploit["command"]
        assert "shell.php" not in exploit["command"].replace("v1__shell.php", "")

    def test_rendered_ability_contains_uploads_block(self):
        """The rendered YAML includes the uploads list for exploit abilities."""
        m = _mod("v1", caldera=_caldera("initial-access"))
        m.caldera["exploit"]["uploads"] = ["shell.php"]
        abilities = _build_abilities([m])
        exploit = [a for a in abilities if a["phase"] == "exploit"][0]

        from jinja2 import Environment, FileSystemLoader
        from builder.caldera import TEMPLATES_DIR
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        rendered = _render_ability(env, exploit)
        assert "uploads:" in rendered
        assert "- v1__shell.php" in rendered

    def test_rendered_ability_without_uploads_has_no_uploads_block(self):
        """Recon abilities (no uploads) render without an uploads block."""
        m = _mod("v1", caldera=_caldera("initial-access"))
        abilities = _build_abilities([m])
        recon = [a for a in abilities if a["phase"] == "recon"][0]

        from jinja2 import Environment, FileSystemLoader
        from builder.caldera import TEMPLATES_DIR
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        rendered = _render_ability(env, recon)
        assert "uploads:" not in rendered


# ── Native fact gating (replaces file-based skip logic) ──

class TestNativeGating:
    def test_recon_ability_gets_default_parser(self):
        """Recon abilities auto-get a parser when the command emits the marker."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="grep x && echo VULNERABLE: yes || echo SECURE"))
        recon = [a for a in _build_abilities([m]) if a["phase"] == "recon"][0]
        assert recon["parsers"]
        parser = recon["parsers"][0]
        assert parser["module"] == "plugins.ctf-exploit.app.parsers.ctf_basic"
        assert parser["mappings"][0]["source"] == fact_trait("v1")
        assert parser["mappings"][0]["custom_parser_vals"]["marker"] == RECON_MARKER

    def test_recon_parser_not_added_without_marker(self):
        """Recon commands that don't emit the marker get no auto parser."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo hello"))
        recon = [a for a in _build_abilities([m]) if a["phase"] == "recon"][0]
        assert recon["parsers"] == []

    def test_explicit_recon_parser_used_instead_of_default(self):
        """A module-declared parser wins over the auto default."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo VULNERABLE: x"))
        m.caldera["recon"]["parser"] = [{
            "module": "custom.module",
            "mappings": [{"source": "custom.trait"}],
        }]
        recon = [a for a in _build_abilities([m]) if a["phase"] == "recon"][0]
        assert recon["parsers"] == [{
            "module": "custom.module",
            "mappings": [{"source": "custom.trait"}],
        }]

    def test_exploit_gets_requirement_and_fact_gate(self):
        """Exploit abilities gate on the recon fact via requirement + command var."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo VULNERABLE: x"))
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["requirements"] == _default_exploit_requirement("v1")
        assert fact_trait("v1") in exploit["command"]
        assert "SKIPPED" in exploit["command"]

    def test_exploit_without_recon_has_no_gate(self):
        """Exploit-only modules (e.g. goals) are not gated on a recon fact."""
        m = _mod("v1", type="goal", caldera={
            "tactic": "impact",
            "technique": {"attack_id": "T1496", "name": "Deface"},
            "exploit": {"description": "deface", "command": "echo defaced"},
        })
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["requirements"] == []
        assert fact_trait("v1") not in exploit["command"]

    def test_explicit_exploit_requirement_wins(self):
        """A module-declared requirement is preserved, not replaced."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo VULNERABLE: x"))
        m.caldera["exploit"]["requirements"] = [{
            "module": "custom.req",
            "mappings": [{"source": "other.trait"}],
        }]
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["requirements"] == [{
            "module": "custom.req",
            "mappings": [{"source": "other.trait"}],
        }]

    def test_gate_command_structure(self):
        """The gate wraps the command with a fact-presence check."""
        gated = _gate_exploit_command("id", "v1")
        assert fact_trait("v1") in gated
        assert "id" in gated
        assert gated.startswith('[ -n "#{')

    def test_default_parsers_module_path(self):
        parsers = _default_recon_parsers("v1", "echo VULNERABLE: x")
        assert parsers[0]["module"] == "plugins.ctf-exploit.app.parsers.ctf_basic"


# ── Path adversary naming ──

class TestPathNaming:
    def test_single_node_path_no_duplicate_name(self):
        """A single-node path produces 'Path N: X' not 'X -> X'."""
        from builder.attack_tree import build_attack_tree
        m = _mod("v1", name="Hidden Files", caldera=_caldera("initial-access"))
        tree = build_attack_tree([m])
        abilities = _build_abilities([m])
        profiles = build_path_adversaries(tree, abilities, "vm-a")
        assert profiles
        assert "Hidden Files -> Hidden Files" not in profiles[0]["name"]
        assert "Hidden Files" in profiles[0]["name"]

    def test_multi_node_path_name(self):
        from builder.attack_tree import build_attack_tree
        m1 = _mod("v1", name="Initial Access", caldera=_caldera("initial-access"))
        m2 = _mod("v2", name="PrivEsc", caldera=_caldera("privilege-escalation"))
        tree = build_attack_tree([m1, m2])
        abilities = _build_abilities([m1, m2])
        profiles = build_path_adversaries(tree, abilities, "vm-a")
        assert any("Initial Access -> PrivEsc" in p["name"] for p in profiles)


# ── Plugin generation ──

class TestPluginGeneration:
    @pytest.fixture
    def export_tmp(self, tmp_path, monkeypatch):
        import builder.caldera as bc
        monkeypatch.setattr(bc, "CALDERA_EXPORTS_DIR", tmp_path)
        return tmp_path

    def test_generate_caldera_export_flat(self, export_tmp):
        """Flat export produces master + per-tactic adversaries."""
        m = _mod("v1", caldera=_caldera("initial-access", attack_id="T1190", technique_name="Exploit App"))
        from builder.selector import select_modules
        quota = {"vulnerability": {"easy": 1}}
        # Directly test via generate_caldera_export with a minimal quota
        import builder.caldera as bc
        bc.load_all_modules = lambda: [m]
        # Select the module regardless of quota via monkeypatched selector
        bc.select_modules = lambda q, lib, base_type_id=None: [m]
        out = bc.generate_caldera_export({"vulnerability": {"easy": 1}}, "test-flat")
        plugin = out / "plugins" / PLUGIN_NAME
        adv_files = list((plugin / "data" / "adversaries").rglob("*.yml"))
        ability_files = list((plugin / "data" / "abilities").rglob("*.yml"))
        assert adv_files
        assert ability_files
        # Master chain present
        names = _adversary_names(adv_files)
        assert "CTF Full Exploit Chain" in names

    def test_generate_caldera_export_multi_path(self, export_tmp):
        """Multi-path export includes legacy + per-path adversaries."""
        import builder.caldera as bc
        m1 = _mod("v1", name="Init", caldera=_caldera("initial-access"))
        m2 = _mod("v2", name="PrivEsc", caldera=_caldera("privilege-escalation"))
        out, tree = bc.generate_caldera_export_multi_path([m1, m2], "test-multi", "vm-a")
        plugin = out / "plugins" / PLUGIN_NAME
        adv_files = list((plugin / "data" / "adversaries").rglob("*.yml"))
        names = _adversary_names(adv_files)
        assert "CTF Full Exploit Chain" in names
        assert any("vm-a Path" in n for n in names)

    def test_generate_caldera_event_export_per_vm(self, export_tmp):
        """Event export generates per-VM path adversaries with hostnames."""
        import builder.caldera as bc
        m1 = _mod("v1", name="Init", caldera=_caldera("initial-access"))
        m2 = _mod("v2", name="PrivEsc", caldera=_caldera("privilege-escalation"))
        vms = {"host-a": [m1], "host-b": [m1, m2]}
        out, trees = bc.generate_caldera_event_export(vms, "test-event")
        plugin = out / "plugins" / PLUGIN_NAME
        adv_files = list((plugin / "data" / "adversaries").rglob("*.yml"))
        names = _adversary_names(adv_files)
        assert set(trees.keys()) == {"host-a", "host-b"}
        assert "CTF Full Exploit Chain" in names
        assert any("host-a Path" in n for n in names)
        assert any("host-b Path" in n for n in names)

    def test_generate_caldera_event_export_no_modules(self, export_tmp):
        """Empty per-VM module map raises ValueError."""
        import builder.caldera as bc
        with pytest.raises(ValueError):
            bc.generate_caldera_event_export({}, "test-empty")

    def test_uploads_file_copied_to_payloads(self, export_tmp):
        """Upload payload files are copied into the plugin payloads dir."""
        import builder.caldera as bc
        tmp = tempfile.mkdtemp()
        payload_path = Path(tmp) / "shell.php"
        payload_path.write_text("<?php ?>")
        m = _mod("v1", caldera=_caldera("initial-access", exploit_cmd="cp ./shell.php /tmp/x.php"))
        m.caldera["exploit"]["uploads"] = ["shell.php"]
        m.source_dir = Path(tmp)
        out = bc.generate_caldera_export_multi_path([m], "test-upload", "vm-a")[0]
        payload_file = out / "plugins" / PLUGIN_NAME / "payloads" / "v1__shell.php"
        assert payload_file.exists()


def _adversary_names(adv_files):
    names = []
    for f in adv_files:
        for line in f.read_text().splitlines():
            if line.startswith("name:"):
                names.append(line.split(":", 1)[1].strip().strip('"'))
    return names


# ── Fact seeding for known VM metadata ──

class TestFactSeeding:
    def test_vm_source_facts_builds_known_metadata(self):
        """vm_source_facts emits hostname/IP/OS/host-id facts from a VM."""
        from api.services.caldera import vm_source_facts

        class FakeVM:
            hostname = "web-01"
            ip_address = "10.0.0.5"
            os = "Ubuntu 24.04"
            id = 7

        facts = vm_source_facts(FakeVM())
        traits = {f["trait"]: f["value"] for f in facts}
        assert traits["ctf.hostname"] == "web-01"
        assert traits["ctf.ip"] == "10.0.0.5"
        assert traits["ctf.os"] == "Ubuntu 24.04"
        assert traits["host.id"] == "7"

    def test_vm_source_facts_skips_empty_fields(self):
        """Facts with empty/None values are excluded."""
        from api.services.caldera import vm_source_facts

        class FakeVM:
            hostname = "web-01"
            ip_address = None
            os = ""
            id = 3

        facts = vm_source_facts(FakeVM())
        assert {"trait": "ctf.hostname", "value": "web-01"} in facts
        assert all(f["trait"] != "ctf.ip" for f in facts)
        assert all(f["trait"] != "ctf.os" for f in facts)

    def test_fact_seeding_renders_into_recon_parser_source(self):
        """The auto recon parser's source trait matches what we seed."""
        from builder.caldera import _build_abilities, fact_trait
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo VULNERABLE: x"))
        recon = [a for a in _build_abilities([m]) if a["phase"] == "recon"][0]
        assert recon["parsers"][0]["mappings"][0]["source"] == fact_trait("v1")
        # Exploit requires the same trait
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["requirements"][0]["mappings"][0]["source"] == fact_trait("v1")


# ── Phase 2a: native objectives + goal achievement detection ──

class TestNativeObjectives:
    """Goal modules produce objectives + goal-achievement parsers."""

    @pytest.fixture
    def export_tmp(self, tmp_path, monkeypatch):
        import builder.caldera as bc
        monkeypatch.setattr(bc, "CALDERA_EXPORTS_DIR", tmp_path)
        return tmp_path

    def test_goal_fact_trait(self):
        from builder.caldera import goal_fact_trait
        assert goal_fact_trait("deface_website") == "ctf.goal.deface_website"

    def test_goal_exploit_gets_default_parser(self):
        """Goal exploit abilities auto-get a parser on the GOAL_ACHIEVED marker."""
        from builder.caldera import GOAL_MARKER, goal_fact_trait
        m = _mod("g1", type="goal", caldera={
            "tactic": "impact",
            "technique": {"attack_id": "T1496", "name": "Deface"},
            "exploit": {"description": "deface", "command": "echo GOAL_ACHIEVED: done"},
        })
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["parsers"]
        parser = exploit["parsers"][0]
        assert parser["module"] == "plugins.ctf-exploit.app.parsers.ctf_basic"
        assert parser["mappings"][0]["source"] == goal_fact_trait("g1")
        assert parser["mappings"][0]["custom_parser_vals"]["marker"] == GOAL_MARKER

    def test_goal_without_marker_parser_still_added(self):
        """install_c2 emits the marker from a payload script, not the command.

        The parser must be added regardless of whether the command string itself
        contains the marker (the payload's stdout still contains it).
        """
        from builder.caldera import goal_fact_trait
        m = _mod("install_c2", type="goal", caldera={
            "tactic": "persistence",
            "technique": {"attack_id": "T1543", "name": "Create Systemd"},
            "exploit": {"description": "c2", "command": "sudo bash ./install_c2.sh"},
        })
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["parsers"]
        assert exploit["parsers"][0]["mappings"][0]["source"] == goal_fact_trait("install_c2")

    def test_explicit_goal_parser_wins_over_default(self):
        m = _mod("g1", type="goal", caldera={
            "tactic": "impact",
            "technique": {"attack_id": "T1496", "name": "Deface"},
            "exploit": {
                "description": "deface",
                "command": "echo GOAL_ACHIEVED: done",
                "parser": [{"module": "custom.module", "mappings": [{"source": "custom.trait"}]}],
            },
        })
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["parsers"] == [{
            "module": "custom.module",
            "mappings": [{"source": "custom.trait"}],
        }]

    def test_vulnerability_exploit_gets_no_goal_parser(self):
        """Non-goal exploits keep their recon-fact parser behaviour (no goal parser)."""
        m = _mod("v1", caldera=_caldera("initial-access"))
        exploit = [a for a in _build_abilities([m]) if a["phase"] == "exploit"][0]
        assert exploit["parsers"] == []

    def test_build_objectives_creates_per_goal_and_combined(self):
        from builder.caldera import _build_objectives, goal_fact_trait, objective_uuid
        goals = [
            _mod("g1", name="Goal One", type="goal", caldera={
                "tactic": "impact",
                "technique": {"attack_id": "T1496", "name": "Deface"},
                "exploit": {"description": "x", "command": "echo GOAL_ACHIEVED"},
            }),
            _mod("g2", name="Goal Two", type="goal", caldera={
                "tactic": "impact",
                "technique": {"attack_id": "T1496", "name": "Deface"},
                "exploit": {"description": "x", "command": "echo GOAL_ACHIEVED"},
            }),
        ]
        objectives, goal_map, combined = _build_objectives(goals)
        # two per-goal objectives + one combined
        assert len(objectives) == 3
        assert goal_map["g1"] == objective_uuid(("g1",))
        assert goal_map["g2"] == objective_uuid(("g2",))
        assert combined == objective_uuid(("g1", "g2"))
        # combined objective lists both goals with operator *
        combined_obj = next(o for o in objectives if o["id"] == combined)
        targets = {g["target"] for g in combined_obj["goals"]}
        assert targets == {goal_fact_trait("g1"), goal_fact_trait("g2")}
        assert all(g["operator"] == "*" and g["count"] == 1 for g in combined_obj["goals"])

    def test_build_objectives_no_goals(self):
        from builder.caldera import _build_objectives
        objectives, goal_map, combined = _build_objectives([_mod("v1")])
        assert objectives == []
        assert goal_map == {}
        assert combined is None

    def test_adversary_profiles_wire_combined_objective(self):
        from builder.caldera import _build_adversary_profiles, objective_uuid
        profiles = _build_adversary_profiles(
            _build_abilities([_mod("v1", caldera=_caldera("initial-access"))]),
            objective_id=objective_uuid(("g1",)),
        )
        assert all(p["objective"] == objective_uuid(("g1",)) for p in profiles)

    def test_path_adversary_wires_terminal_goal_objective(self):
        from builder.attack_tree import build_attack_tree
        from builder.caldera import objective_uuid
        vuln = _mod("v1", name="Init", caldera=_caldera("initial-access"))
        goal = _mod("g1", name="Deface", type="goal", caldera={
            "tactic": "impact",
            "technique": {"attack_id": "T1496", "name": "Deface"},
            "exploit": {"description": "x", "command": "echo GOAL_ACHIEVED"},
        })
        goal.requires = ["v1"]
        tree = build_attack_tree([vuln, goal])
        abilities = _build_abilities([vuln, goal])
        goal_map = {"g1": objective_uuid(("g1",))}
        profiles = build_path_adversaries(tree, abilities, "vm-a", goal_map)
        # the path ends at the goal, so its objective is the goal's objective
        path_profiles = [p for p in profiles if "Path" in p["name"]]
        assert path_profiles
        assert all(p["objective"] == objective_uuid(("g1",)) for p in path_profiles)

    def test_path_adversary_no_goal_no_objective(self):
        from builder.attack_tree import build_attack_tree
        m1 = _mod("v1", name="Init", caldera=_caldera("initial-access"))
        tree = build_attack_tree([m1])
        abilities = _build_abilities([m1])
        profiles = build_path_adversaries(tree, abilities, "vm-a", {})
        path_profiles = [p for p in profiles if "Path" in p["name"]]
        assert path_profiles
        assert all(p["objective"] is None for p in path_profiles)

    def test_objective_yaml_files_written(self, export_tmp):
        """Goal objectives are written into the plugin data/objectives dir."""
        import yaml
        import builder.caldera as bc
        from builder.caldera import goal_fact_trait, objective_uuid
        vulns, goals = self._real_modules()
        out, _ = bc.generate_caldera_event_export(
            {"vm-a": vulns[:2] + goals}, "integration-objectives"
        )
        obj_dir = out / "plugins" / PLUGIN_NAME / "data" / "objectives"
        files = list(obj_dir.rglob("*.yml"))
        assert files
        # combined objective references every goal's trait
        combined_file = obj_dir / f"{objective_uuid(tuple(sorted(g.id for g in goals)))}.yml"
        assert combined_file.exists()
        combined = yaml.safe_load(combined_file.read_text())
        assert set(g["target"] for g in combined["goals"]) == {
            goal_fact_trait(g.id) for g in goals
        }

    def test_adversary_yaml_wires_objective(self, export_tmp):
        """Adversary YAML includes the objective field when goals exist."""
        import yaml
        import builder.caldera as bc
        from builder.caldera import objective_uuid
        vulns, goals = self._real_modules()
        out, _ = bc.generate_caldera_event_export(
            {"vm-a": vulns[:2] + goals}, "integration-adversary-objective"
        )
        adv_dir = out / "plugins" / PLUGIN_NAME / "data" / "adversaries"
        combined = objective_uuid(tuple(sorted(g.id for g in goals)))
        master_file = adv_dir / f"{bc._adversary_uuid('master')}.yml"
        master = yaml.safe_load(master_file.read_text())
        assert master["objective"] == combined

    def _real_modules(self):
        from builder.module_loader import load_all_modules
        lib = load_all_modules()
        vulns = [m for m in lib if m.caldera and m.type in ("vulnerability", "payload")]
        goals = [m for m in lib if m.caldera and m.type == "goal"]
        assert vulns, "test requires vulnerability modules with caldera metadata"
        assert goals, "test requires goal modules with caldera metadata"
        return vulns, goals


# ── Phase 1 integration: native fact gating end-to-end ──

class TestNativeFactGatingIntegration:
    """Exercise the full plugin generation path with real module library."""

    @pytest.fixture
    def export_tmp(self, tmp_path, monkeypatch):
        import builder.caldera as bc
        monkeypatch.setattr(bc, "CALDERA_EXPORTS_DIR", tmp_path)
        return tmp_path

    def _real_modules(self):
        from builder.module_loader import load_all_modules
        lib = load_all_modules()
        vulns = [m for m in lib if m.caldera and m.type in ("vulnerability", "payload")]
        goals = [m for m in lib if m.caldera and m.type == "goal"]
        assert vulns, "test requires vulnerability modules with caldera metadata"
        return vulns, goals

    def test_generated_abilities_are_valid_yaml_with_gating(self, export_tmp):
        """Every generated ability parses as YAML; vulns are gated, goals are not."""
        import yaml
        import builder.caldera as bc
        vulns, goals = self._real_modules()
        out, trees = bc.generate_caldera_event_export(
            {"vm-a": vulns + goals}, "integration-1"
        )
        plugin = out / "plugins" / PLUGIN_NAME

        recon_gated = 0
        exploit_gated = 0
        exploit_requirements = 0
        ungated_goals = 0
        for ability_file in (plugin / "data" / "abilities").rglob("*.yml"):
            docs = list(yaml.safe_load_all(ability_file.read_text()))
            for doc in docs:
                ab = doc[0] if isinstance(doc, list) else doc
                if not ab:
                    continue
                name = ab.get("name", "")
                executor = (ab.get("platforms") or {}).get("linux", {}).get("sh", {})
                command = executor.get("command", "")
                parsers = executor.get("parsers") or {}
                reqs = ab.get("requirements") or []
                if name.startswith("Recon:"):
                    # Recons whose command emits the marker must have a parser
                    if RECON_MARKER in command:
                        assert parsers, f"{name} missing auto parser"
                        recon_gated += 1
                elif name.startswith("Exploit:"):
                    if "#{" in command:
                        exploit_gated += 1
                    if reqs:
                        exploit_requirements += 1
                    if "#{" not in command:
                        ungated_goals += 1

        assert recon_gated > 0
        # Every gated exploit has both the requirement and the fact variable
        assert exploit_gated == exploit_requirements
        # Goal exploits (no recon) are ungated
        assert ungated_goals >= len(goals)

    def test_gate_references_same_trait_as_requirement(self, export_tmp):
        """Exploit command's #{...} trait matches its requirement's source."""
        import yaml
        import builder.caldera as bc
        vulns, _ = self._real_modules()
        out, _ = bc.generate_caldera_event_export({"vm-a": vulns[:5]}, "integration-2")
        plugin = out / "plugins" / PLUGIN_NAME
        for ability_file in (plugin / "data" / "abilities").rglob("*.yml"):
            for doc in yaml.safe_load_all(ability_file.read_text()):
                ab = doc[0] if isinstance(doc, list) else doc
                if not ab or not ab.get("name", "").startswith("Exploit:"):
                    continue
                executor = (ab.get("platforms") or {}).get("linux", {}).get("sh", {})
                command = executor.get("command", "")
                reqs = ab.get("requirements") or []
                if not reqs:
                    continue
                # v0 format: [{module: [mappings]}]
                mappings = list(reqs[0].values())[0]
                req_source = mappings[0]["source"]
                # the command references the same trait via #{trait}
                assert f"#{{{req_source}}}" in command, (
                    f"{ab['name']} requirement source {req_source} not referenced in command"
                )

    def test_plugin_app_staged(self, export_tmp):
        """Parser + requirement modules are staged under plugin app/ dir."""
        import builder.caldera as bc
        vulns, _ = self._real_modules()
        out, _ = bc.generate_caldera_event_export({"vm-a": vulns[:2]}, "integration-3")
        plugin = out / "plugins" / PLUGIN_NAME
        assert (plugin / "app" / "parsers" / "ctf_basic.py").exists()
        assert (plugin / "app" / "requirements" / "fact_present.py").exists()
        # no base_requirement.py (dashed-import hazard)
        assert not (plugin / "app" / "requirements" / "base_requirement.py").exists()

    def test_fact_present_module_has_no_dashed_imports(self):
        """fact_present.py must not import plugins.ctf-exploit.* (invalid syntax)."""
        from builder.caldera import CALDERA_PLUGIN_APP_DIR
        fact_present = CALDERA_PLUGIN_APP_DIR / "requirements" / "fact_present.py"
        src = fact_present.read_text()
        assert "from plugins.ctf-exploit" not in src
        assert "import plugins.ctf-exploit" not in src
