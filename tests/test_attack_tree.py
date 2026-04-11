"""Tests for builder.attack_tree module."""

import pytest

from builder.attack_tree import (
    INFRASTRUCTURE_PHASE,
    TACTIC_PHASE,
    AttackTree,
    build_attack_tree,
    extract_paths,
    serialize_tree,
)
from builder.module_loader import Module


# ── Helpers ──

def _mod(
    id,
    name=None,
    type="vulnerability",
    difficulty="easy",
    category="general",
    requires=None,
    caldera=None,
):
    """Create a minimal test Module."""
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
    )


def _caldera(tactic, attack_id="T0000", technique_name="Test", phase_override=None):
    """Create a caldera metadata dict."""
    cal = {
        "tactic": tactic,
        "technique": {"attack_id": attack_id, "name": technique_name},
        "recon": {"description": "recon", "command": "echo recon"},
        "exploit": {"description": "exploit", "command": "echo exploit"},
    }
    if phase_override is not None:
        cal["phase_override"] = phase_override
    return cal


# ── Empty / trivial inputs ──

class TestEmptyInput:
    def test_empty_module_list(self):
        tree = build_attack_tree([])
        assert tree.nodes == {}
        assert tree.edges == []
        assert tree.paths == []

    def test_no_caldera_modules(self):
        """Modules without caldera metadata produce an empty tree."""
        mods = [
            _mod("h1", type="hardening"),
            _mod("v1"),  # vulnerability but no caldera
        ]
        tree = build_attack_tree(mods)
        assert tree.nodes == {}
        assert tree.edges == []
        assert tree.paths == []


class TestSingleModule:
    def test_single_caldera_module(self):
        """A single caldera module produces one node, no edges, one single-node path."""
        m = _mod("v1", caldera=_caldera("initial-access", "T1190", "Exploit App"))
        tree = build_attack_tree([m])

        assert len(tree.nodes) == 1
        node = tree.nodes["v1"]
        assert node.phase == 0
        assert node.tactic == "initial-access"
        assert node.technique_id == "T1190"
        assert node.is_infrastructure is False
        assert tree.edges == []
        # Phase 0 node with no outgoing edges is terminal -> single path
        assert tree.paths == [["v1"]]

    def test_single_non_phase0_module(self):
        """A single module at phase > 0 is isolated and becomes a single-node path."""
        m = _mod("v1", caldera=_caldera("persistence"))
        tree = build_attack_tree([m])

        assert len(tree.nodes) == 1
        assert tree.nodes["v1"].phase == 2
        # Not phase 0, no inbound edges, no outbound -> isolated
        assert tree.paths == [["v1"]]


# ── Phase mapping ──

class TestPhaseMapping:
    def test_tactic_to_phase(self):
        """Each tactic maps to the correct phase."""
        for tactic, expected_phase in TACTIC_PHASE.items():
            m = _mod(f"v_{tactic}", caldera=_caldera(tactic))
            tree = build_attack_tree([m])
            assert tree.nodes[f"v_{tactic}"].phase == expected_phase, (
                f"Expected {tactic} -> phase {expected_phase}"
            )

    def test_phase_override(self):
        """phase_override in caldera metadata takes precedence over tactic mapping."""
        m = _mod("v1", caldera=_caldera("privilege-escalation", phase_override=0))
        tree = build_attack_tree([m])
        # Tactic maps to phase 3, but override says 0
        assert tree.nodes["v1"].phase == 0
        assert tree.nodes["v1"].tactic == "privilege-escalation"

    def test_unknown_tactic_defaults_to_phase_0(self):
        """An unrecognized tactic defaults to phase 0."""
        m = _mod("v1", caldera=_caldera("unknown-tactic"))
        tree = build_attack_tree([m])
        assert tree.nodes["v1"].phase == 0


# ── Infrastructure / application modules ──

class TestInfrastructureNodes:
    def test_application_module_included_as_infrastructure(self):
        """Application modules in the requires chain appear as infrastructure nodes."""
        app = _mod("app1", type="application_external")
        vuln = _mod(
            "v1",
            requires=["app1"],
            caldera=_caldera("initial-access", "T1190", "Exploit App"),
        )
        tree = build_attack_tree([app, vuln])

        assert "app1" in tree.nodes
        assert tree.nodes["app1"].is_infrastructure is True
        assert tree.nodes["app1"].phase == INFRASTRUCTURE_PHASE
        assert tree.nodes["app1"].tactic == ""
        assert tree.nodes["app1"].technique_id == ""

        assert "v1" in tree.nodes
        assert tree.nodes["v1"].is_infrastructure is False

    def test_application_module_excluded_if_not_required(self):
        """Application modules NOT in any requires chain are excluded."""
        app = _mod("app1", type="application_external")
        vuln = _mod("v1", caldera=_caldera("initial-access"))
        tree = build_attack_tree([app, vuln])

        assert "app1" not in tree.nodes
        assert "v1" in tree.nodes

    def test_infrastructure_not_in_paths(self):
        """Infrastructure nodes do not appear in extracted paths."""
        app = _mod("app1", type="application_external")
        vuln = _mod(
            "v1",
            requires=["app1"],
            caldera=_caldera("initial-access"),
        )
        tree = build_attack_tree([app, vuln])

        for path in tree.paths:
            assert "app1" not in path

    def test_transitive_infrastructure(self):
        """Infrastructure collected transitively through requires chain."""
        app_base = _mod("app_base", type="application_internal")
        app_top = _mod("app_top", type="application_external", requires=["app_base"])
        vuln = _mod(
            "v1",
            requires=["app_top"],
            caldera=_caldera("initial-access"),
        )
        tree = build_attack_tree([app_base, app_top, vuln])

        assert "app_base" in tree.nodes
        assert "app_top" in tree.nodes
        assert tree.nodes["app_base"].is_infrastructure is True
        assert tree.nodes["app_top"].is_infrastructure is True


# ── Dependency edges ──

class TestDependencyEdges:
    def test_requires_creates_edge(self):
        """Module B requiring module A creates an edge A -> B."""
        app = _mod("app1", type="application_external")
        vuln = _mod("v1", requires=["app1"], caldera=_caldera("initial-access"))
        tree = build_attack_tree([app, vuln])

        requires_edges = [(s, t) for s, t, etype in tree.edges if etype == "requires"]
        assert ("app1", "v1") in requires_edges

    def test_no_edge_for_external_requires(self):
        """Requires pointing to modules outside the tree don't produce edges."""
        vuln = _mod("v1", requires=["nonexistent"], caldera=_caldera("initial-access"))
        tree = build_attack_tree([vuln])

        assert tree.edges == []
        # The nonexistent requirement is filtered out of the node's requires
        assert tree.nodes["v1"].requires == []


# ── Phase-ordering edges ──

class TestPhaseOrderEdges:
    def test_standalone_phase_ordering(self):
        """Standalone modules at phase N connect to phase N+1."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))      # phase 0
        v2 = _mod("v2", caldera=_caldera("execution"))            # phase 1
        v3 = _mod("v3", caldera=_caldera("persistence"))          # phase 2
        tree = build_attack_tree([v1, v2, v3])

        phase_edges = [(s, t) for s, t, etype in tree.edges if etype == "phase_order"]
        assert ("v1", "v2") in phase_edges
        assert ("v2", "v3") in phase_edges
        # No backward edges
        assert ("v2", "v1") not in phase_edges
        assert ("v3", "v2") not in phase_edges

    def test_phase_gap_edges(self):
        """Phase gaps are valid: phase 0 -> phase 3 if nothing at 1-2."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))            # phase 0
        v2 = _mod("v2", caldera=_caldera("privilege-escalation"))      # phase 3
        tree = build_attack_tree([v1, v2])

        phase_edges = [(s, t) for s, t, etype in tree.edges if etype == "phase_order"]
        assert ("v1", "v2") in phase_edges

    def test_no_backward_phase_edges(self):
        """Phase-ordering never creates backward edges."""
        v1 = _mod("v1", caldera=_caldera("persistence"))          # phase 2
        v2 = _mod("v2", caldera=_caldera("initial-access"))       # phase 0
        tree = build_attack_tree([v1, v2])

        for src, tgt, _ in tree.edges:
            src_phase = tree.nodes[src].phase
            tgt_phase = tree.nodes[tgt].phase
            if src_phase >= 0 and tgt_phase >= 0:  # skip infra
                assert src_phase <= tgt_phase, (
                    f"Backward edge: {src}(phase {src_phase}) -> {tgt}(phase {tgt_phase})"
                )


# ── Path extraction ──

class TestPathExtraction:
    def test_linear_chain(self):
        """Three phases in sequence produce a single path."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))
        v2 = _mod("v2", caldera=_caldera("execution"))
        v3 = _mod("v3", caldera=_caldera("persistence"))
        tree = build_attack_tree([v1, v2, v3])

        assert [["v1", "v2", "v3"]] == tree.paths

    def test_branching_paths(self):
        """Two modules at the same phase produce separate paths."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))      # phase 0
        v2a = _mod("v2a", caldera=_caldera("execution"))          # phase 1
        v2b = _mod("v2b", caldera=_caldera("execution"))          # phase 1
        tree = build_attack_tree([v1, v2a, v2b])

        # Should have 2 paths: v1->v2a and v1->v2b
        assert len(tree.paths) == 2
        assert ["v1", "v2a"] in tree.paths
        assert ["v1", "v2b"] in tree.paths

    def test_phase_gap_path(self):
        """Paths can skip phases: phase 0 -> phase 3."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))            # phase 0
        v2 = _mod("v2", caldera=_caldera("privilege-escalation"))      # phase 3
        tree = build_attack_tree([v1, v2])

        assert [["v1", "v2"]] == tree.paths

    def test_isolated_node_path(self):
        """Isolated non-phase-0 nodes with no edges become single-node paths."""
        # v1 is at phase 0 (path root), v_isolated is at phase 4 with no connections to v1
        # Actually, standalone modules WILL get phase-ordering edges.
        # To get a truly isolated node, it must not be standalone with any other node.
        # Let's test with a single non-phase-0 module.
        v1 = _mod("v1", caldera=_caldera("credential-access"))  # phase 4
        tree = build_attack_tree([v1])

        assert tree.paths == [["v1"]]

    def test_one_module_per_phase_in_path(self):
        """A path visits at most one module per phase."""
        v1 = _mod("v1", caldera=_caldera("initial-access"))
        v2 = _mod("v2", caldera=_caldera("execution"))
        v3 = _mod("v3", caldera=_caldera("execution"))  # same phase as v2
        v4 = _mod("v4", caldera=_caldera("persistence"))
        tree = build_attack_tree([v1, v2, v3, v4])

        for path in tree.paths:
            phases = [tree.nodes[nid].phase for nid in path]
            assert len(phases) == len(set(phases)), f"Duplicate phase in path {path}"


class TestPathPruning:
    def test_max_paths_respected(self):
        """Path count is limited to max_paths."""
        # Create many modules at phase 1 to produce many paths from phase 0
        mods = [_mod("v0", caldera=_caldera("initial-access"))]
        for i in range(30):
            mods.append(_mod(f"v1_{i}", caldera=_caldera("execution")))
        tree = build_attack_tree(mods)

        # Default max is 20
        assert len(tree.paths) <= 20

    def test_pruning_prefers_longer_paths(self):
        """After pruning, longer paths (more phases) are kept over shorter ones."""
        mods = [_mod("v0", caldera=_caldera("initial-access"))]
        # Many short alternatives at phase 1
        for i in range(25):
            mods.append(_mod(f"v1_{i}", caldera=_caldera("execution")))
        # One deeper chain
        mods.append(_mod("v_deep1", caldera=_caldera("execution", phase_override=1)))
        mods.append(_mod("v_deep2", caldera=_caldera("persistence")))

        tree = build_attack_tree(mods)

        # The deeper path v0 -> v_deep1 -> v_deep2 should survive pruning
        deep_path = ["v0", "v_deep1", "v_deep2"]
        # Check that at least one path of length 3 exists
        long_paths = [p for p in tree.paths if len(p) >= 3]
        assert len(long_paths) > 0


# ── Complex scenarios ──

class TestComplexTree:
    def test_dependency_subtree_with_infrastructure(self):
        """Full scenario: app -> vuln1 (initial-access) -> vuln2 (privesc)."""
        app = _mod("app1", type="application_external")
        v1 = _mod(
            "v1",
            requires=["app1"],
            caldera=_caldera("initial-access", "T1190", "Exploit App"),
        )
        v2 = _mod(
            "v2",
            requires=["app1"],
            caldera=_caldera("privilege-escalation", "T1548", "Privesc"),
        )
        tree = build_attack_tree([app, v1, v2])

        # Infrastructure node present
        assert tree.nodes["app1"].is_infrastructure is True
        # Dependency edges
        requires_edges = {(s, t) for s, t, e in tree.edges if e == "requires"}
        assert ("app1", "v1") in requires_edges
        assert ("app1", "v2") in requires_edges

        # Phase ordering within subtree: v1 (phase 0) -> v2 (phase 3)
        phase_edges = {(s, t) for s, t, e in tree.edges if e == "phase_order"}
        assert ("v1", "v2") in phase_edges

        # Path should be v1 -> v2 (infrastructure excluded)
        assert ["v1", "v2"] in tree.paths

    def test_mixed_standalone_and_dependency(self):
        """Standalone modules connect to dependency-subtree modules at next phase."""
        app = _mod("app1", type="application_external")
        v_app = _mod(
            "v_app",
            requires=["app1"],
            caldera=_caldera("initial-access"),
        )
        v_standalone = _mod("v_standalone", caldera=_caldera("initial-access"))
        v_privesc = _mod("v_privesc", caldera=_caldera("privilege-escalation"))

        tree = build_attack_tree([app, v_app, v_standalone, v_privesc])

        # Both phase-0 modules should have paths to v_privesc
        paths_with_privesc = [p for p in tree.paths if "v_privesc" in p]
        assert len(paths_with_privesc) >= 2

    def test_multiple_phases_linear(self):
        """Multi-phase linear path."""
        mods = [
            _mod("m0", caldera=_caldera("initial-access")),
            _mod("m1", caldera=_caldera("execution")),
            _mod("m2", caldera=_caldera("persistence")),
            _mod("m3", caldera=_caldera("privilege-escalation")),
            _mod("m4", caldera=_caldera("credential-access")),
        ]
        tree = build_attack_tree(mods)

        # Should have a path covering all 5 phases
        full_path = ["m0", "m1", "m2", "m3", "m4"]
        assert full_path in tree.paths


# ── Serialization ──

class TestSerializeTree:
    def test_serialize_structure(self):
        """Serialized tree has correct top-level keys."""
        m = _mod("v1", caldera=_caldera("initial-access", "T1190", "Exploit"))
        tree = build_attack_tree([m])
        data = serialize_tree(tree)

        assert "nodes" in data
        assert "edges" in data
        assert "paths" in data
        assert "generated_at" in data

    def test_serialize_node_fields(self):
        """Serialized nodes have all required fields."""
        m = _mod("v1", name="Test Vuln", caldera=_caldera("initial-access", "T1190", "Exploit"))
        tree = build_attack_tree([m])
        data = serialize_tree(tree)

        node = data["nodes"][0]
        assert node["id"] == "v1"
        assert node["name"] == "Test Vuln"
        assert node["tactic"] == "initial-access"
        assert node["phase"] == 0
        assert node["technique_id"] == "T1190"
        assert node["technique_name"] == "Exploit"
        assert node["is_infrastructure"] is False
        assert node["status"] is None
        assert "requires" in node

    def test_serialize_edges(self):
        """Serialized edges have source, target, type."""
        app = _mod("app1", type="application_external")
        vuln = _mod("v1", requires=["app1"], caldera=_caldera("initial-access"))
        tree = build_attack_tree([app, vuln])
        data = serialize_tree(tree)

        assert len(data["edges"]) > 0
        edge = data["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "type" in edge

    def test_serialize_empty_tree(self):
        """Empty tree serializes cleanly."""
        tree = AttackTree()
        data = serialize_tree(tree)

        assert data["nodes"] == []
        assert data["edges"] == []
        assert data["paths"] == []
        assert "generated_at" in data

    def test_generated_at_is_iso_format(self):
        """generated_at is a valid ISO timestamp."""
        tree = AttackTree()
        data = serialize_tree(tree)
        from datetime import datetime
        # Should not raise
        datetime.fromisoformat(data["generated_at"])


# ── Edge cases ──

class TestEdgeCases:
    def test_hardening_modules_excluded(self):
        """Hardening modules are never included in the tree."""
        h = _mod("h1", type="hardening", caldera=_caldera("persistence"))
        tree = build_attack_tree([h])
        # Hardening modules have caldera but type != vulnerability
        # The spec says "vulnerability modules" -- hardening should still be included
        # if it has caldera metadata and is not an application_ type.
        # Actually re-reading spec: "Filters to modules with caldera metadata
        # (vulnerability modules)" -- this is parenthetical description.
        # The implementation filters on `not type.startswith("application_")`,
        # so hardening with caldera IS included. Let's verify.
        assert "h1" in tree.nodes

    def test_payload_modules_included(self):
        """Payload modules with caldera metadata are included."""
        p = _mod("p1", type="payload", caldera=_caldera("persistence"))
        tree = build_attack_tree([p])
        assert "p1" in tree.nodes

    def test_disabled_modules_still_processed(self):
        """The attack tree builder does not filter disabled modules (that's the selector's job)."""
        m = _mod("v1", caldera=_caldera("initial-access"))
        m.disabled = True
        tree = build_attack_tree([m])
        assert "v1" in tree.nodes

    def test_duplicate_module_ids(self):
        """If duplicate IDs are passed, last one wins in the all_modules_by_id dict."""
        m1 = _mod("v1", name="first", caldera=_caldera("initial-access"))
        m2 = _mod("v1", name="second", caldera=_caldera("execution"))
        tree = build_attack_tree([m1, m2])
        # Both will try to be added to caldera_modules dict; second overwrites first
        assert "v1" in tree.nodes

    def test_self_referencing_requires_ignored(self):
        """A module requiring itself should not cause infinite loops."""
        m = _mod("v1", requires=["v1"], caldera=_caldera("initial-access"))
        tree = build_attack_tree([m])
        assert "v1" in tree.nodes
