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
GOAL_PHASE = 8  # Terminal objectives; always higher than any attack phase


# ── Data structures ──

@dataclass
class AttackNode:
    module_id: str
    module_name: str
    tactic: str           # from caldera.tactic (empty string for infrastructure)
    phase: int            # from tactic mapping, phase_override, or GOAL_PHASE
    technique_id: str     # e.g., "T1548.003" (empty string for infrastructure)
    technique_name: str   # (empty string for infrastructure)
    is_infrastructure: bool  # True for application modules
    is_goal: bool = False    # True for goal-type modules
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
    children: dict[str, list[str]] = defaultdict(list)
    for nid, node in nodes.items():
        for req in node.requires:
            if req in nodes:
                children[req].append(nid)

    roots = []
    for nid, node in nodes.items():
        internal_parents = [r for r in node.requires if r in nodes]
        if not internal_parents:
            roots.append(nid)

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
        if len(tree_members) > 1:
            subtrees[root] = tree_members
            assigned.update(tree_members)

    standalone = set(nodes.keys()) - assigned
    if standalone:
        subtrees[None] = standalone

    return subtrees


# ── Core functions ──

def build_attack_tree(modules: list[Module]) -> AttackTree:
    """Build an attack tree from a list of Module objects.

    Includes:
    - vulnerability/payload modules with caldera metadata (attack nodes, phases 0-7)
    - goal-type modules with caldera metadata (terminal nodes, phase 8)
    - application modules in the requires chain (infrastructure, phase -1)

    Paths run from phase-0 attack nodes through the kill chain and terminate
    at goal nodes.
    """
    tree = AttackTree()

    all_modules_by_id: dict[str, Module] = {m.id: m for m in modules}

    # Step 1: Separate caldera attack modules (vulns/payloads) from goal modules
    attack_modules: dict[str, Module] = {}
    goal_modules: dict[str, Module] = {}

    for m in modules:
        if not m.caldera:
            continue
        if m.type == "goal":
            goal_modules[m.id] = m
        elif not m.type.startswith("application_"):
            attack_modules[m.id] = m

    if not attack_modules and not goal_modules:
        return tree

    all_caldera = {**attack_modules, **goal_modules}

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

    for m in all_caldera.values():
        _collect_infra(m)

    # Step 3: Create AttackNodes for attack modules (phases 0-7)
    for m in attack_modules.values():
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
            is_goal=False,
            requires=[r for r in m.requires if r in attack_modules or r in infra_ids],
        )

    # Step 3b: Create AttackNodes for goal modules (always at GOAL_PHASE)
    for m in goal_modules.values():
        cal = m.caldera
        technique = cal.get("technique", {})
        goal_requires = [
            r for r in m.requires
            if r in attack_modules or r in infra_ids
        ]

        tree.nodes[m.id] = AttackNode(
            module_id=m.id,
            module_name=m.name,
            tactic=cal.get("tactic", "impact"),
            phase=GOAL_PHASE,
            technique_id=technique.get("attack_id", ""),
            technique_name=technique.get("name", ""),
            is_infrastructure=False,
            is_goal=True,
            requires=goal_requires,
        )

    # Step 3c: Create AttackNodes for infrastructure
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
            is_goal=False,
            requires=[r for r in m.requires if r in all_caldera or r in infra_ids],
        )

    # Step 4: Create edges
    # 4a: Dependency edges (including goal→attack and goal→infra)
    for nid, node in tree.nodes.items():
        for req_id in node.requires:
            if req_id in tree.nodes:
                tree.edges.append((req_id, nid, "requires"))

    # 4b: Phase-ordering edges among attack nodes only (exclude goals from subtree logic)
    attack_nodes = {nid: n for nid, n in tree.nodes.items()
                    if not n.is_infrastructure and not n.is_goal}
    subtrees = _find_subtrees(attack_nodes)

    by_phase: dict[int, list[str]] = defaultdict(list)
    for nid, node in attack_nodes.items():
        by_phase[node.phase].append(nid)

    edge_set = {(s, t) for s, t, _ in tree.edges}

    for root_id, members in subtrees.items():
        non_goal_members = [m for m in members if m in attack_nodes]
        if not non_goal_members:
            continue

        member_by_phase: dict[int, list[str]] = defaultdict(list)
        for mid in non_goal_members:
            member_by_phase[attack_nodes[mid].phase].append(mid)

        member_phases = sorted(member_by_phase.keys())

        for i in range(len(member_phases) - 1):
            current_phase = member_phases[i]
            next_phase = member_phases[i + 1]
            for src in member_by_phase[current_phase]:
                for tgt in member_by_phase[next_phase]:
                    if (src, tgt) not in edge_set:
                        tree.edges.append((src, tgt, "phase_order"))
                        edge_set.add((src, tgt))

    standalone = subtrees.get(None, set())
    standalone_non_goal = [s for s in standalone if s in attack_nodes]

    standalone_by_phase: dict[int, list[str]] = defaultdict(list)
    for sid in standalone_non_goal:
        standalone_by_phase[attack_nodes[sid].phase].append(sid)

    standalone_phases = sorted(standalone_by_phase.keys())
    for i in range(len(standalone_phases) - 1):
        current_phase = standalone_phases[i]
        next_phase = standalone_phases[i + 1]
        for src in standalone_by_phase[current_phase]:
            for tgt in standalone_by_phase[next_phase]:
                if (src, tgt) not in edge_set:
                    tree.edges.append((src, tgt, "phase_order"))
                    edge_set.add((src, tgt))

    dep_nodes_by_phase: dict[int, list[str]] = defaultdict(list)
    for root_id, members in subtrees.items():
        if root_id is None:
            continue
        for mid in members:
            if mid in attack_nodes:
                dep_nodes_by_phase[attack_nodes[mid].phase].append(mid)

    all_phases = sorted(set(by_phase.keys()))

    for sid in standalone_non_goal:
        src_phase = attack_nodes[sid].phase
        for p in all_phases:
            if p > src_phase and p in dep_nodes_by_phase:
                for tgt in dep_nodes_by_phase[p]:
                    if (sid, tgt) not in edge_set:
                        tree.edges.append((sid, tgt, "phase_order"))
                        edge_set.add((sid, tgt))
                break

    for root_id, members in subtrees.items():
        if root_id is None:
            continue
        non_goal_members = [m for m in members if m in attack_nodes]
        outgoing = set()
        for s, t, _ in tree.edges:
            if s in members and t in members:
                outgoing.add(s)
        terminals = [m for m in non_goal_members if m not in outgoing]
        for term in terminals:
            term_phase = attack_nodes[term].phase
            for p in sorted(standalone_by_phase.keys()):
                if p > term_phase:
                    for tgt in standalone_by_phase[p]:
                        if (term, tgt) not in edge_set:
                            tree.edges.append((term, tgt, "phase_order"))
                            edge_set.add((term, tgt))
                    break

    # Step 4c: Wire goal nodes to the attack graph.
    # Goals with explicit requires on attack nodes already have dependency edges (Step 4a).
    # Goals without explicit attack-node requires get phase_order edges from all terminal
    # attack nodes (nodes with no outgoing edges to other attack nodes).
    if goal_modules:
        attack_outgoing = {
            s for s, t, _ in tree.edges
            if s in attack_nodes and t in attack_nodes
        }
        terminal_attack_nodes = [nid for nid in attack_nodes if nid not in attack_outgoing]

        for goal_id, goal_node in tree.nodes.items():
            if not goal_node.is_goal:
                continue
            has_attack_inbound = any(
                s in attack_nodes for s, t, _ in tree.edges if t == goal_id
            )
            if not has_attack_inbound:
                for term in terminal_attack_nodes:
                    if (term, goal_id) not in edge_set:
                        tree.edges.append((term, goal_id, "phase_order"))
                        edge_set.add((term, goal_id))

    # Step 5: Extract paths
    tree.paths = extract_paths(tree)

    return tree


def extract_paths(tree: AttackTree, max_paths: int = 20) -> list[list[str]]:
    """Extract attack paths via DFS from phase-0 nodes to terminal nodes (goals or leaf attacks).

    Infrastructure nodes (phase -1) are excluded from paths.
    Isolated non-phase-0 attack nodes with no inbound edges become single-node paths.
    Paths prefer terminating at goal nodes.
    """
    if not tree.nodes:
        return []

    traversable = {nid: n for nid, n in tree.nodes.items() if not n.is_infrastructure}

    if not traversable:
        return []

    adj: dict[str, list[str]] = defaultdict(list)
    for src, tgt, _ in tree.edges:
        if src in traversable and tgt in traversable:
            adj[src].append(tgt)

    terminals = {nid for nid in traversable if not adj.get(nid)}

    has_inbound: set[str] = set()
    for src, tgt, _ in tree.edges:
        if src in traversable and tgt in traversable:
            has_inbound.add(tgt)

    phase0 = [nid for nid, n in traversable.items() if n.phase == 0]

    isolated = [
        nid for nid in traversable
        if nid not in has_inbound
        and nid not in phase0
        and not adj.get(nid)
        and not tree.nodes[nid].is_goal
    ]

    paths: list[list[str]] = []

    def dfs(current: str, path: list[str], visited_phases: set[int]) -> None:
        current_phase = traversable[current].phase
        new_path = path + [current]
        new_visited = visited_phases | {current_phase}

        if current in terminals:
            paths.append(new_path)
            return

        for neighbor in adj.get(current, []):
            neighbor_phase = traversable[neighbor].phase
            if not traversable[neighbor].is_goal:
                if neighbor_phase in new_visited:
                    continue
                if neighbor_phase < current_phase:
                    continue
            dfs(neighbor, new_path, new_visited)

    for start in phase0:
        dfs(start, [], set())

    for iso in isolated:
        paths.append([iso])

    if len(paths) > max_paths:
        paths = _prune_paths(paths, max_paths)

    return paths


def _prune_paths(paths: list[list[str]], max_paths: int) -> list[list[str]]:
    """Prune paths to max_paths, prioritizing longer kill chains and deduplicating."""
    paths.sort(key=lambda p: len(p), reverse=True)

    kept: list[list[str]] = []
    seen_suffixes: set[tuple[str, ...]] = set()

    for path in paths:
        if len(kept) >= max_paths:
            break
        suffix_len = max(1, len(path) // 2)
        suffix = tuple(path[-suffix_len:])
        if suffix not in seen_suffixes:
            kept.append(path)
            seen_suffixes.add(suffix)

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
                "is_goal": node.is_goal,
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


# ── Operation result annotation ──

RECON_SUCCESS_MARKER = "VULNERABLE"


def annotate_tree_statuses(tree_data: dict, links: list[dict], recon_marker: str = RECON_SUCCESS_MARKER) -> dict:
    """Annotate serialized tree nodes with operation result statuses.

    ``links`` is a list of annotated operation chain links, each with
    ``module_id``, ``phase`` ("recon"/"exploit"), ``status`` (Caldera link
    status), ``finish`` (timestamp), and ``output`` (str).

    Classification per module (exploit status wins over recon):
      - exploit link finished with status 0            → "succeeded"
      - exploit link finished with non-zero status     → "failed"
      - exploit link whose output starts with "SKIPPED:" → "skipped"
      - exploit link still collecting (status -3)      → "pending"
      - recon ran but the recon output did not contain the success marker →
          "skipped"  (exploit was trimmed at planning because the recon fact
          was absent — the native fact-gating equivalent of a skip)
      - recon ran and output contains the marker       → "pending"
      - no link data for the module                    → None

    Returns a new dict with ``nodes`` carrying a ``status`` value.
    """
    module_status: dict[str, str] = {}
    module_exploit_seen: set[str] = set()
    module_recon_output: dict[str, str] = {}

    for link in links:
        mid = link.get("module_id")
        if not mid:
            continue
        phase = link.get("phase", "")
        status = link.get("status")
        output = link.get("output", "")
        finish = link.get("finish")
        if phase == "recon":
            module_recon_output[mid] = output
            continue
        module_exploit_seen.add(mid)
        if output and output.startswith("SKIPPED:"):
            module_status[mid] = "skipped"
        elif finish and status == 0:
            module_status[mid] = "succeeded"
        elif finish and status != 0:
            module_status[mid] = "failed"
        elif status == -3:
            module_status[mid] = "pending"
        else:
            module_status[mid] = "pending"

    # Infer status for modules that have a recon link but no exploit link.
    for node in tree_data["nodes"]:
        mid = node["id"]
        if mid in module_status:
            continue
        recon_out = module_recon_output.get(mid, "")
        if recon_out:
            if recon_marker not in recon_out:
                module_status[mid] = "skipped"
            else:
                module_status[mid] = "pending"

    annotated = dict(tree_data)
    annotated["nodes"] = [
        dict(node, status=module_status.get(node["id"]))
        for node in tree_data["nodes"]
    ]
    return annotated
