"""Attack tree data model and algorithms for Caldera integration.

Builds a directed graph of attack nodes from selected modules, ordered by
ATT&CK tactic phase. Extracts linear attack paths through the graph for
adversary generation and UI rendering.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from builder.module_loader import Module


# ── Tactic-to-phase mapping ──

TACTIC_PHASE: dict[str, int] = {
    "initial-access": 0,
    "execution": 1,
    "persistence": 2,
    "privilege-escalation": 3,
    "credential-access": 4,
    "collection": 5,
    "impact": 6,
    "command-and-control": 7,
}

INFRASTRUCTURE_PHASE = -1


# ── Data structures ──

@dataclass
class AttackNode:
    module_id: str
    module_name: str
    tactic: str           # from caldera.tactic (empty string for infrastructure)
    phase: int            # from tactic mapping or phase_override
    technique_id: str     # e.g., "T1548.003" (empty string for infrastructure)
    technique_name: str   # (empty string for infrastructure)
    is_infrastructure: bool  # True for application modules
    requires: list[str] = field(default_factory=list)


@dataclass
class AttackTree:
    nodes: dict[str, AttackNode] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)


# ── Helper: find dependency subtrees ──

def _find_subtrees(nodes: dict[str, AttackNode]) -> dict[str | None, set[str]]:
    """Group nodes into dependency subtrees.

    Each subtree is identified by the root ancestor module_id (the node with
    no requires within the tree). Standalone nodes (no requires, and no
    dependents) map to key None.

    Returns a dict: root_id | None -> set of module_ids in that subtree.
    """
    # Build parent->children map
    children: dict[str, list[str]] = defaultdict(list)
    for nid, node in nodes.items():
        for req in node.requires:
            if req in nodes:
                children[req].append(nid)

    # Find roots: nodes whose requires are either empty or point outside the tree
    roots = []
    for nid, node in nodes.items():
        internal_parents = [r for r in node.requires if r in nodes]
        if not internal_parents:
            roots.append(nid)

    # BFS from each root to find its subtree
    subtrees: dict[str | None, set[str]] = {}
    assigned: set[str] = set()

    for root in roots:
        tree_members: set[str] = set()
        queue: deque[str] = deque([root])
        while queue:
            current = queue.popleft()
            if current in tree_members:
                continue
            tree_members.add(current)
            for child in children.get(current, []):
                queue.append(child)
        # Only consider it a "dependency subtree" if it has more than one member
        # or if the root has children (meaning it's an infrastructure node)
        if len(tree_members) > 1:
            subtrees[root] = tree_members
            assigned.update(tree_members)
        # Single-member "subtrees" with no dependents are standalone

    # Standalone nodes
    standalone = set(nodes.keys()) - assigned
    if standalone:
        subtrees[None] = standalone

    return subtrees


# ── Core functions ──

def build_attack_tree(modules: list[Module]) -> AttackTree:
    """Build an attack tree from a list of Module objects.

    Includes vulnerability/payload modules that have caldera metadata, plus
    application modules that are in the requires chain of any included module.
    """
    tree = AttackTree()

    # Step 1: Identify caldera modules (vulnerabilities/payloads with caldera metadata)
    caldera_modules: dict[str, Module] = {}
    all_modules_by_id: dict[str, Module] = {m.id: m for m in modules}

    for m in modules:
        if m.caldera and not m.type.startswith("application_"):
            caldera_modules[m.id] = m

    if not caldera_modules:
        return tree

    # Step 2: Collect required infrastructure modules (application_* in requires chain)
    infra_ids: set[str] = set()

    def _collect_infra(mod: Module) -> None:
        for req_id in mod.requires:
            if req_id in infra_ids:
                continue
            req_mod = all_modules_by_id.get(req_id)
            if req_mod and req_mod.type.startswith("application_"):
                infra_ids.add(req_id)
                _collect_infra(req_mod)

    for m in caldera_modules.values():
        _collect_infra(m)

    # Step 3: Create AttackNodes
    for m in caldera_modules.values():
        cal = m.caldera
        tactic = cal["tactic"]
        phase_override = cal.get("phase_override")
        phase = phase_override if phase_override is not None else TACTIC_PHASE.get(tactic, 0)
        technique = cal.get("technique", {})

        tree.nodes[m.id] = AttackNode(
            module_id=m.id,
            module_name=m.name,
            tactic=tactic,
            phase=phase,
            technique_id=technique.get("attack_id", ""),
            technique_name=technique.get("name", ""),
            is_infrastructure=False,
            requires=[r for r in m.requires if r in caldera_modules or r in infra_ids],
        )

    for infra_id in infra_ids:
        m = all_modules_by_id[infra_id]
        tree.nodes[infra_id] = AttackNode(
            module_id=m.id,
            module_name=m.name,
            tactic="",
            phase=INFRASTRUCTURE_PHASE,
            technique_id="",
            technique_name="",
            is_infrastructure=True,
            requires=[r for r in m.requires if r in caldera_modules or r in infra_ids],
        )

    # Step 4: Create edges
    # 4a: Dependency edges
    for nid, node in tree.nodes.items():
        for req_id in node.requires:
            if req_id in tree.nodes:
                tree.edges.append((req_id, nid, "requires"))

    # 4b: Phase-ordering edges
    subtrees = _find_subtrees(tree.nodes)

    # Non-infrastructure nodes grouped by phase
    non_infra = {nid: n for nid, n in tree.nodes.items() if not n.is_infrastructure}

    by_phase: dict[int, list[str]] = defaultdict(list)
    for nid, node in non_infra.items():
        by_phase[node.phase].append(nid)

    sorted_phases = sorted(by_phase.keys())

    # Existing edge set for deduplication
    edge_set = {(s, t) for s, t, _ in tree.edges}

    # For each subtree, add phase-ordering edges within the subtree
    for root_id, members in subtrees.items():
        non_infra_members = [m for m in members if m in non_infra]
        if not non_infra_members:
            continue

        # Group members by phase
        member_by_phase: dict[int, list[str]] = defaultdict(list)
        for mid in non_infra_members:
            member_by_phase[non_infra[mid].phase].append(mid)

        member_phases = sorted(member_by_phase.keys())

        for i in range(len(member_phases) - 1):
            current_phase = member_phases[i]
            next_phase = member_phases[i + 1]
            for src in member_by_phase[current_phase]:
                for tgt in member_by_phase[next_phase]:
                    if (src, tgt) not in edge_set:
                        tree.edges.append((src, tgt, "phase_order"))
                        edge_set.add((src, tgt))

    # Standalone nodes also connect to dependency-subtree modules at next phase
    standalone = subtrees.get(None, set())
    standalone_non_infra = [s for s in standalone if s in non_infra]

    # For standalone modules: connect phase N to phase N+1 (all standalone at those phases)
    standalone_by_phase: dict[int, list[str]] = defaultdict(list)
    for sid in standalone_non_infra:
        standalone_by_phase[non_infra[sid].phase].append(sid)

    standalone_phases = sorted(standalone_by_phase.keys())
    for i in range(len(standalone_phases) - 1):
        current_phase = standalone_phases[i]
        next_phase = standalone_phases[i + 1]
        for src in standalone_by_phase[current_phase]:
            for tgt in standalone_by_phase[next_phase]:
                if (src, tgt) not in edge_set:
                    tree.edges.append((src, tgt, "phase_order"))
                    edge_set.add((src, tgt))

    # Standalone nodes at phase N connect to dependency-subtree nodes at next adjacent phase
    # Collect all non-standalone non-infra nodes by phase
    dep_nodes_by_phase: dict[int, list[str]] = defaultdict(list)
    for root_id, members in subtrees.items():
        if root_id is None:
            continue
        for mid in members:
            if mid in non_infra:
                dep_nodes_by_phase[non_infra[mid].phase].append(mid)

    all_phases = sorted(set(by_phase.keys()))

    for sid in standalone_non_infra:
        src_phase = non_infra[sid].phase
        # Find the next phase that has dependency-subtree nodes
        for p in all_phases:
            if p > src_phase and p in dep_nodes_by_phase:
                for tgt in dep_nodes_by_phase[p]:
                    if (sid, tgt) not in edge_set:
                        tree.edges.append((sid, tgt, "phase_order"))
                        edge_set.add((sid, tgt))
                break  # Only connect to the next adjacent phase

    # Also connect dependency-subtree terminal nodes to standalone nodes at next phase
    # Terminal = nodes in a subtree with no outgoing edges within the subtree
    for root_id, members in subtrees.items():
        if root_id is None:
            continue
        non_infra_members = [m for m in members if m in non_infra]
        outgoing = set()
        for s, t, _ in tree.edges:
            if s in members and t in members:
                outgoing.add(s)
        terminals = [m for m in non_infra_members if m not in outgoing]
        for term in terminals:
            term_phase = non_infra[term].phase
            for p in sorted(standalone_by_phase.keys()):
                if p > term_phase:
                    for tgt in standalone_by_phase[p]:
                        if (term, tgt) not in edge_set:
                            tree.edges.append((term, tgt, "phase_order"))
                            edge_set.add((term, tgt))
                    break

    # Step 5: Extract paths
    tree.paths = extract_paths(tree)

    return tree


def extract_paths(tree: AttackTree, max_paths: int = 20) -> list[list[str]]:
    """Extract attack paths via DFS from phase-0 nodes to terminal nodes.

    Infrastructure nodes (phase -1) are excluded from paths.
    Isolated non-phase-0 nodes with no inbound edges become single-node paths.
    """
    if not tree.nodes:
        return []

    non_infra = {nid: n for nid, n in tree.nodes.items() if not n.is_infrastructure}

    if not non_infra:
        return []

    # Build adjacency list (non-infrastructure only)
    adj: dict[str, list[str]] = defaultdict(list)
    for src, tgt, _ in tree.edges:
        if src in non_infra and tgt in non_infra:
            adj[src].append(tgt)

    # Find terminal nodes (no outgoing edges among non-infra)
    terminals = {nid for nid in non_infra if not adj.get(nid)}

    # Find nodes with inbound edges
    has_inbound: set[str] = set()
    for src, tgt, _ in tree.edges:
        if src in non_infra and tgt in non_infra:
            has_inbound.add(tgt)

    # Phase 0 nodes are path roots
    phase0 = [nid for nid, n in non_infra.items() if n.phase == 0]

    # Isolated nodes: no inbound edges and not phase 0, and no outbound edges
    isolated = [
        nid for nid in non_infra
        if nid not in has_inbound and nid not in phase0 and not adj.get(nid)
    ]

    paths: list[list[str]] = []

    # DFS from each phase-0 node
    def dfs(current: str, path: list[str], visited_phases: set[int]) -> None:
        current_phase = non_infra[current].phase
        new_path = path + [current]
        new_visited = visited_phases | {current_phase}

        if current in terminals:
            paths.append(new_path)

        for neighbor in adj.get(current, []):
            neighbor_phase = non_infra[neighbor].phase
            # At most one module per phase in a path
            if neighbor_phase in new_visited:
                continue
            # Never go backward in phase
            if neighbor_phase < current_phase:
                continue
            dfs(neighbor, new_path, new_visited)

    for start in phase0:
        dfs(start, [], set())

    # Isolated nodes become single-node paths
    for iso in isolated:
        paths.append([iso])

    # Pruning if too many paths
    if len(paths) > max_paths:
        paths = _prune_paths(paths, max_paths)

    return paths


def _prune_paths(paths: list[list[str]], max_paths: int) -> list[list[str]]:
    """Prune paths to max_paths, prioritizing longer kill chains and deduplicating."""
    # Sort by path length descending (equivalent to phase count due to one-per-phase constraint)
    paths.sort(key=lambda p: len(p), reverse=True)

    # Deduplicate shared suffixes: if two paths share the same suffix, keep the longer one
    kept: list[list[str]] = []
    seen_suffixes: set[tuple[str, ...]] = set()

    for path in paths:
        if len(kept) >= max_paths:
            break
        # Use last half of path as suffix key for deduplication
        suffix_len = max(1, len(path) // 2)
        suffix = tuple(path[-suffix_len:])
        if suffix not in seen_suffixes:
            kept.append(path)
            seen_suffixes.add(suffix)

    # If we still need more paths after dedup, add remaining
    if len(kept) < max_paths:
        for path in paths:
            if path not in kept and len(kept) < max_paths:
                kept.append(path)

    return kept[:max_paths]


def serialize_tree(tree: AttackTree) -> dict:
    """Serialize an AttackTree to a JSON-compatible dict."""
    return {
        "nodes": [
            {
                "id": node.module_id,
                "name": node.module_name,
                "tactic": node.tactic,
                "phase": node.phase,
                "technique_id": node.technique_id,
                "technique_name": node.technique_name,
                "is_infrastructure": node.is_infrastructure,
                "requires": node.requires,
                "status": None,
            }
            for node in tree.nodes.values()
        ],
        "edges": [
            {"source": src, "target": tgt, "type": etype}
            for src, tgt, etype in tree.edges
        ],
        "paths": tree.paths,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
