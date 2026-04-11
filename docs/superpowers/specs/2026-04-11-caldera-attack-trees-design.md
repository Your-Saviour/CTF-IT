# Caldera Attack Tree Integration

## Context

The CTF platform assigns randomized vulnerabilities to each VM, which means each target has a unique combination of initial access vectors, privilege escalation paths, and post-exploitation opportunities. The current Caldera integration treats all attacks as a flat list -- one "CTF Full Exploit Chain" adversary runs all recon then all exploits in sequence, with no concept of branching, phase ordering, or adaptive behavior. This makes red team operations unrealistic and gives admins no visibility into which attack paths exist or were taken.

This design introduces **attack trees** -- directed acyclic graphs that model the relationships between a VM's vulnerabilities as multi-path kill chains. Caldera adversary profiles are generated per-path, and a visual attack graph gives admins real-time visibility into which paths were attempted and which succeeded.

## Kill Chain Phase Mapping

Modules are ordered by their existing ATT&CK `tactic` field, mapped to kill chain phases:

| Phase | ATT&CK Tactic | Role |
|-------|---------------|------|
| -1 | (application modules) | Infrastructure -- no attack step, enables dependents |
| 0 | `initial-access` | Gain foothold |
| 1 | `execution` | Run code |
| 2 | `persistence` | Maintain access |
| 3 | `privilege-escalation` | Elevate to root |
| 4 | `credential-access` | Steal credentials |
| 5 | `collection` | Gather data |
| 6 | `impact` | Cause damage |
| 7 | `command-and-control` | Establish C2 |

Modules may include an optional `phase_override: <int>` in their YAML to override the default mapping when the tactic doesn't cleanly correspond to the desired position in the kill chain.

## Attack Tree Data Model

### Nodes

Each node wraps a module with caldera metadata. Application modules appear only as infrastructure context nodes (no attack abilities) when they are in the `requires` chain of a vulnerability node.

```python
@dataclass
class AttackNode:
    module_id: str
    module_name: str
    tactic: str           # from caldera.tactic
    phase: int            # from tactic mapping or phase_override
    technique_id: str     # e.g., "T1548.003"
    technique_name: str
    is_infrastructure: bool  # True for application modules
    requires: list[str]   # module IDs this depends on
```

### Edges

Two types of edges connect nodes:

1. **Dependency edges** (`requires`): Module B requires Module A. If A is an application (infrastructure), B can only be attacked if A is present. These are explicit from the YAML.
2. **Phase-ordering edges**: Modules at phase N enable modules at phase N+1, but only when they share a dependency ancestor or are both standalone (same-host implicit connection). This prevents false connections between unrelated dependency subtrees.

Edge creation rules:
- If B `requires` A, create edge A -> B
- For standalone modules (no `requires`): every standalone module at phase N connects to every standalone module at phase N+1. These are implicitly linked by being on the same host.
- For modules within the same dependency subtree (sharing a common `requires` ancestor): create phase-ordering edges between adjacent phases within that subtree
- Standalone modules also connect to dependency-subtree modules at the next phase (a standalone initial-access can lead to a web-app-specific priv-esc if both are on the VM)
- Do NOT create edges that go backward in phase order

### Tree Structure

```python
@dataclass
class AttackTree:
    nodes: dict[str, AttackNode]       # keyed by module_id
    edges: list[tuple[str, str, str]]  # (source_id, target_id, edge_type)
    paths: list[list[str]]             # extracted attack paths (lists of module_ids)
```

### Serialization

The tree serializes to JSON for storage and frontend rendering:

```json
{
  "nodes": [
    {
      "id": "inventory_default_creds",
      "name": "Inventory Default Credentials",
      "tactic": "initial-access",
      "phase": 0,
      "technique_id": "T1078",
      "is_infrastructure": false,
      "status": null
    }
  ],
  "edges": [
    {"source": "inventory_dashboard", "target": "inventory_default_creds", "type": "requires"},
    {"source": "inventory_default_creds", "target": "nopasswd_sudo", "type": "phase_order"}
  ],
  "paths": [
    ["inventory_default_creds", "nopasswd_sudo", "world_writable_shadow"],
    ["inventory_default_creds", "nopasswd_sudo", "flask_defacement"]
  ]
}
```

## Path Extraction

Paths are extracted via DFS from every initial-access node (phase 0) through to every reachable terminal node.

**Rules:**
- A path visits at most one module per phase (alternatives are separate paths)
- Paths that cross phase gaps are valid (e.g., phase 0 -> phase 3 if nothing exists at phases 1-2)
- Isolated nodes (no inbound edges, not initial-access) become single-node paths
- Maximum path count: 20 (configurable). Pruning prioritizes paths covering more distinct phases, then deduplicates shared suffixes.

## Adversary Generation

Each extracted path becomes a Caldera adversary profile.

**Adversary naming:** `CTF {vm_hostname} Path {N}: {first_module} -> {last_module}`

**Ability ordering within a path adversary:** For each module in path order, the adversary's `atomic_ordering` includes: recon ability, then exploit ability. This gives the sequence: recon_A, exploit_A, recon_B, exploit_B, recon_C, exploit_C.

**Phase-gated execution (try one, move on):** Caldera's `atomic` planner runs abilities in strict sequence and does not support conditional skipping. We embed skip logic in shell commands:

- Each **recon** ability, on success, writes a marker: `echo "PHASE_{N}_SUCCESS" > /tmp/.ctf_phase_{N}`
- Each **exploit** ability checks its recon succeeded: `test -f /tmp/.ctf_phase_{N} || { echo "SKIPPED: recon failed"; exit 0; }`
- Alternative recon abilities at the same phase check if the phase is already complete: `test -f /tmp/.ctf_phase_{N} && { echo "SKIPPED: phase already completed"; exit 0; }`

This means within a single adversary, only the first successful module at each phase runs its exploit. Alternatives are skipped. The output string "SKIPPED:" is used by the results annotation layer to classify the ability as skipped rather than succeeded.

**Preserved profiles:**
- The existing "CTF Full Exploit Chain" master adversary is kept for backward compatibility (flat list, all abilities)
- Per-tactic adversaries are kept
- New per-path adversaries are added alongside them

## Caldera Setup Flow Changes

The current `caldera-setup` endpoint accepts `event_id` and creates one adversary + one operation. The new flow:

1. Accept `event_id` (setup all VMs in the event)
2. For each VM with assigned modules (`VMModule`):
   a. Build the attack tree from the VM's modules
   b. Extract paths
   c. Generate per-path adversary profiles
   d. Store the tree JSON on the VM record (`attack_tree_json` column)
3. Write all abilities and adversaries into a single plugin export
4. Copy plugin, restart Caldera once
5. Verify abilities loaded
6. Create one operation per VM using the VM's first path adversary (admin can switch adversaries in Caldera UI)

This batches all VMs into a single Caldera restart, avoiding N restarts for N VMs.

## New API Endpoints

### `GET /admin/caldera/attack-tree/{vm_id}`

Returns the attack tree JSON for a VM, computed from its current `VMModule` assignments. If the VM has a cached `attack_tree_json`, returns that with an `is_stale` flag if the generation timestamp predates the latest module assignment change.

### `GET /admin/caldera/operations/{op_id}?include_tree=true`

Existing endpoint, enhanced: when `include_tree=true`, annotates the tree nodes with operation result statuses (succeeded/failed/skipped/pending) by matching ability UUIDs back to module IDs.

## Database Changes

Add one column to `VM`:

```python
attack_tree_json: Mapped[str] = mapped_column(Text, nullable=True)
```

This stores the serialized attack tree with generation timestamp. No new tables.

## Visual Attack Tree UI

### Technology

**elkjs** (Eclipse Layout Kernel for JS) via CDN for DAG layout, rendered onto an SVG with D3.js. elkjs is actively maintained and handles hierarchical left-to-right layouts well.

### Rendering

- **Layout:** Left-to-right flow. Phase columns with headers (Initial Access, Priv Esc, etc.)
- **Nodes:** Rounded rectangles. Label = module name. Badge = tactic.
- **Node colors:**
  - Green fill: exploit succeeded
  - Red border: defended (recon found vuln, exploit failed or was remediated)
  - Gray fill: not attempted (path skipped)
  - Cyan fill with pulse animation: currently running
  - White fill: pending (not yet reached)
- **Edges:**
  - Solid green arrow: path taken and succeeded
  - Solid red arrow: path taken but failed
  - Dashed gray arrow: path exists but not taken
- **Interaction:**
  - Click node: side panel shows ability output (recon output, exploit output)
  - Hover: tooltip with module description, technique ID, phase
  - Toggle: "All paths" vs "Executed path only"

### Placement

1. **VM detail page** (`/admin/vm/{id}`): new "Attack Graph" section below module progress
2. **Caldera operation detail**: graph view tab alongside existing results table, nodes colored by live results
3. **Caldera dashboard list view**: thumbnail attack graph per VM in the VM status table

### Template Structure

A reusable partial `attack_tree_partial.html` that accepts tree JSON and optional result annotations. Included by the VM detail and Caldera dashboard templates.

## Module YAML Additions

One optional new field:

```yaml
phase_override: 3  # override tactic-based phase ordering
```

No other YAML changes needed. Existing `caldera.tactic`, `caldera.technique`, `caldera.recon`, `caldera.exploit`, and `requires` fields provide all necessary metadata.

## File Changes Summary

**New files:**
- `builder/attack_tree.py` -- tree construction, path extraction, serialization
- `api/routes/caldera_tree.py` -- attack tree API endpoint
- `frontend/templates/attack_tree_partial.html` -- reusable graph visualization partial
- `tests/test_attack_tree.py` -- unit tests for tree logic

**Modified files:**
- `builder/caldera.py` -- add `build_path_adversaries()`, integrate with attack tree
- `api/routes/caldera_setup.py` -- per-VM adversary generation, batch setup, store tree JSON
- `api/routes/caldera_ops.py` -- annotate tree with operation results
- `api/models.py` -- add `attack_tree_json` column to VM
- `api/main.py` -- register caldera_tree router
- `frontend/templates/vm_detail.html` -- embed attack tree section
- `frontend/templates/caldera_dashboard.html` -- add tree view to operation detail
- `templates/caldera_ability.yml.j2` -- add skip-logic preamble to commands

## Verification Plan

### Unit tests (`tests/test_attack_tree.py`)
- Tree construction from known module sets produces correct nodes and edges
- Path extraction produces expected paths with correct ordering
- Pruning limits path count and preserves coverage
- Modules without caldera metadata are excluded (except as infrastructure nodes)
- Phase gaps handled correctly
- Isolated nodes produce single-node paths

### Integration tests
- `generate_caldera_export()` with `multi_path=True` produces correct plugin directory structure
- Per-path adversary YAMLs contain correct `atomic_ordering`
- Ability YAMLs contain skip-logic preambles
- Attack tree API endpoint returns valid JSON matching the schema

### Manual E2E
- Run `caldera-setup` for an event with multiple VMs
- Verify multiple adversary profiles appear in Caldera
- Run a path adversary operation against a VM
- Verify: first successful initial access causes alternatives to be skipped
- Verify: operation results annotate the tree correctly in the UI
- Verify: attack tree graph renders with correct node colors
- Test with VMs that have: many modules, few modules, only standalone vulns, only web app chain vulns

### Edge cases to test
- VM with no caldera-enabled modules (tree is empty, handled gracefully)
- VM with one module (single-node tree, single-node path)
- VM with only application modules and no vulns (empty tree)
- Module YAML with `phase_override` (overrides tactic mapping)
