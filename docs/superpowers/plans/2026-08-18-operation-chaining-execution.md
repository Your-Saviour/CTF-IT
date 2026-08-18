# Operation Chaining Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a curated operation-plan graph (RCE → privesc → implant) as a single success-gated, data-flowing sweep driven by the platform, one ability at a time against live VMs via single-ability Caldera operations.

**Architecture:** Three new pure-logic modules (`builder/fact_contract.py`, `builder/operation_compiler.py`) plus a runtime service (`api/services/operation_runner.py`) and a thin Caldera driver (`api/services/operation_driver.py`). The runner persists run/step state in two new models, compiles the authored plan into a resolved graph, and drives each node. Data flows between stages through a platform-owned fact store; stage outputs are parsed from Caldera output and consumed as `#{trait}` inputs by later stages.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, httpx (async Caldera client), Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-operation-chaining-execution-design.md`

## Global Constraints

- Python 3.12; dependencies are pinned in `requirements.txt` (no new third-party deps).
- Module YAML additions (`outputs`/`inputs` under `caldera.recon`/`caldera.exploit`) are optional and backward-compatible.
- Existing recon→exploit gating and goal facts must keep working with no module rewrites.
- Scoring (blue defensive/reactive, red offensive) is untouched; `objective` nodes are observability/gate only.
- Authoritative test command is `docker compose --profile test run --rm tests`; local `pytest tests/<file>` is used for fast red/green cycles. Do not import Python modules directly outside pytest.
- No new raw user-input surface; ability commands are pre-authored in module YAML.
- Follow existing code patterns: `require_admin(request, db)` for admin auth, `asyncio.create_task` for background work, `utcnow()` for timestamps.

---

### Task 1: Fact contract core

Create the pure fact-contract module that turns a module's Caldera metadata into output/input fact specs and provides substitution + extraction. No Caldera, no DB.

**Files:**
- Create: `builder/fact_contract.py`
- Test: `tests/test_fact_contract.py`

**Interfaces:**
- Consumes: `builder.module_loader.Module` (has `.id`, `.type`, `.caldera` dict).
- Produces (imported by Tasks 2, 3, 6, 9):
  - `recon_fact_trait(module_id: str) -> str` → `f"ctf.vuln.{module_id}"`
  - `goal_fact_trait(goal_id: str) -> str` → `f"ctf.goal.{goal_id}"`
  - `PLATFORM_FACT_TRAITS: set[str]` → `{"ctf.hostname", "ctf.ip", "ctf.os", "host.id"}`
  - `FactSpec` dataclass: `trait: str`, `marker: str = ""`, `pattern: str = ""`, `group: int = 1`
  - `AbilityFacts` dataclass: `outputs: list[FactSpec]`, `inputs: list[str]`
  - `ability_facts(module: Module, phase: str) -> AbilityFacts`
  - `substitute_command(command: str, fact_store: dict[str, str]) -> str`
  - `extract_facts(output: str, specs: list[FactSpec]) -> dict[str, str]`
  - `emitted_traits(module: Module) -> set[str]`
  - `validate_module_facts(module: Module) -> list[str]`
  - `validate_catalogue_facts(modules: list[Module]) -> dict[str, list[str]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fact_contract.py
from types import SimpleNamespace

from builder.fact_contract import (
    AbilityFacts, FactSpec, ability_facts, extract_facts,
    substitute_command, validate_catalogue_facts, validate_module_facts,
)


def mod(module_id, caldera, type_="vulnerability"):
    return SimpleNamespace(id=module_id, type=type_, caldera=caldera)


def test_recon_auto_derives_vuln_fact():
    m = mod("weak_ssh", {"recon": {"command": "echo VULNERABLE user=x"}})
    facts = ability_facts(m, "recon")
    assert facts.outputs == [FactSpec(trait="ctf.vuln.weak_ssh", marker="VULNERABLE")]
    assert facts.inputs == []


def test_exploit_auto_derives_input_from_recon():
    m = mod("weak_ssh", {
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {"command": "su x"},
    })
    facts = ability_facts(m, "exploit")
    assert facts.inputs == ["ctf.vuln.weak_ssh"]
    assert facts.outputs == []


def test_goal_exploit_auto_derives_goal_fact():
    m = mod("install_c2", {"exploit": {"command": "echo GOAL_ACHIEVED"}}, type_="goal")
    facts = ability_facts(m, "exploit")
    assert facts.outputs == [FactSpec(trait="ctf.goal.install_c2", marker="GOAL_ACHIEVED")]


def test_explicit_outputs_and_inputs_take_precedence():
    m = mod("nopasswd_sudo", {
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {
            "command": "sudo id",
            "inputs": ["ctf.weak_ssh.shell"],
            "outputs": [{"trait": "ctf.nopasswd_sudo.root", "marker": "ROOT_SHELL"}],
        },
    })
    facts = ability_facts(m, "exploit")
    assert facts.inputs == ["ctf.weak_ssh.shell"]
    assert facts.outputs == [FactSpec(trait="ctf.nopasswd_sudo.root", marker="ROOT_SHELL")]


def test_parser_mappings_derive_outputs():
    m = mod("weak_ssh", {
        "recon": {
            "command": "echo VULNERABLE user=svc",
            "parser": [{"module": "x", "mappings": [{
                "source": "ctf.vuln.weak_ssh",
                "custom_parser_vals": {"marker": "VULNERABLE", "pattern": "user=(\\S+)"},
            }]}],
        },
        "exploit": {"command": "su x"},
    })
    facts = ability_facts(m, "recon")
    assert facts.outputs == [FactSpec(trait="ctf.vuln.weak_ssh", marker="VULNERABLE", pattern="user=(\\S+)")]


def test_substitute_command_replaces_traits():
    assert substitute_command("su #{ctf.user.creds}", {"ctf.user.creds": "svc"}) == "su svc"


def test_extract_facts_captures_group_and_marker():
    specs = [FactSpec(trait="ctf.vuln.x", marker="VULNERABLE", pattern="user=(\\S+)")]
    assert extract_facts("noise\nVULNERABLE user=svc-monitor", specs) == {"ctf.vuln.x": "svc-monitor"}


def test_validate_rejects_colliding_trait():
    errors = validate_module_facts(mod("a", {
        "exploit": {"command": "x", "outputs": [{"trait": "ctf.b.root"}]},
    }))
    assert any("ctf.a." in e for e in errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'builder.fact_contract'`

- [ ] **Step 3: Write the module**

```python
# builder/fact_contract.py
from __future__ import annotations

import re
from dataclasses import dataclass, field

from builder.module_loader import Module

RECON_MARKER = "VULNERABLE"
GOAL_MARKER = "GOAL_ACHIEVED"
PLATFORM_FACT_TRAITS = {"ctf.hostname", "ctf.ip", "ctf.os", "host.id"}

TRAIT_REF = re.compile(r"#\{([^}]+)\}")


def recon_fact_trait(module_id: str) -> str:
    return f"ctf.vuln.{module_id}"


def goal_fact_trait(goal_id: str) -> str:
    return f"ctf.goal.{goal_id}"


@dataclass
class FactSpec:
    trait: str
    marker: str = ""
    pattern: str = ""
    group: int = 1


@dataclass
class AbilityFacts:
    outputs: list[FactSpec] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)


def _parser_mappings(section: dict) -> list[dict]:
    raw = section.get("parser")
    if not raw:
        return []
    if isinstance(raw, list):
        mappings = []
        for entry in raw:
            mappings.extend(entry.get("mappings", []))
        return mappings
    return raw.get("mappings", []) or [raw]


def _requirement_sources(section: dict) -> list[str]:
    raw = section.get("requirements", [])
    if not raw:
        return []
    sources = []
    for entry in raw:
        if "mappings" in entry:
            sources.extend(m.get("source") for m in entry["mappings"])
        else:
            sources.append(entry.get("source"))
    return [s for s in sources if s]


def _spec_from_mapping(mapping: dict) -> FactSpec | None:
    trait = mapping.get("source")
    if not trait:
        return None
    vals = mapping.get("custom_parser_vals") or {}
    return FactSpec(
        trait=trait,
        marker=vals.get("marker", ""),
        pattern=vals.get("pattern", ""),
        group=int(vals.get("group", 1)),
    )


def ability_facts(module: Module, phase: str) -> AbilityFacts:
    cal = module.caldera or {}
    section = cal.get(phase, {}) or {}
    command = section.get("command", "")

    outputs: list[FactSpec] = []
    if "outputs" in section:
        outputs = [FactSpec(
            trait=o["trait"],
            marker=o.get("marker", ""),
            pattern=o.get("pattern", ""),
            group=int(o.get("group", 1)),
        ) for o in section["outputs"]]
    elif _parser_mappings(section):
        outputs = [s for m in _parser_mappings(section) if (s := _spec_from_mapping(m))]
    elif phase == "recon" and RECON_MARKER in command:
        outputs = [FactSpec(recon_fact_trait(module.id), marker=RECON_MARKER)]
    elif phase == "exploit" and module.type == "goal" and GOAL_MARKER in command:
        outputs = [FactSpec(goal_fact_trait(module.id), marker=GOAL_MARKER)]

    inputs: list[str] = []
    if "inputs" in section:
        inputs = [i for i in section["inputs"] if isinstance(i, str)]
    elif _requirement_sources(section):
        inputs = _requirement_sources(section)
    elif phase == "exploit" and (cal.get("recon") or {}).get("command"):
        inputs = [recon_fact_trait(module.id)]

    return AbilityFacts(outputs=outputs, inputs=inputs)


def substitute_command(command: str, fact_store: dict[str, str]) -> str:
    return TRAIT_REF.sub(lambda m: fact_store.get(m.group(1), ""), command)


def extract_facts(output: str, specs: list[FactSpec]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in output.splitlines():
        for spec in specs:
            if spec.marker and spec.marker not in line:
                continue
            if spec.pattern:
                match = re.search(spec.pattern, line)
                if not match:
                    continue
                try:
                    value = match.group(spec.group)
                except IndexError:
                    continue
                found[spec.trait] = value
            else:
                found[spec.trait] = spec.marker or "1"
    return found


def emitted_traits(module: Module) -> set[str]:
    traits: set[str] = set()
    for phase in ("recon", "exploit"):
        for spec in ability_facts(module, phase).outputs:
            traits.add(spec.trait)
    return traits


def validate_module_facts(module: Module) -> list[str]:
    errors: list[str] = []
    for phase in ("recon", "exploit"):
        section = (module.caldera or {}).get(phase) or {}
        for out in section.get("outputs", []):
            trait = out.get("trait", "")
            prefix = f"ctf.{module.id}."
            if not trait.startswith(prefix) and trait != goal_fact_trait(module.id) \
                    and trait != recon_fact_trait(module.id):
                errors.append(f"{phase}.outputs trait '{trait}' must be namespaced '{prefix}...'")
            pattern = out.get("pattern")
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{phase}.outputs pattern invalid: {exc}")
    return errors


def validate_catalogue_facts(modules: list[Module]) -> dict[str, list[str]]:
    available = set(PLATFORM_FACT_TRAITS)
    for module in modules:
        available |= emitted_traits(module)
    result: dict[str, list[str]] = {}
    for module in modules:
        errors = validate_module_facts(module)
        for phase in ("recon", "exploit"):
            for trait in ability_facts(module, phase).inputs:
                if trait not in available:
                    errors.append(f"{phase}.inputs references unknown trait '{trait}'")
        if errors:
            result[module.id] = errors
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fact_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add builder/fact_contract.py tests/test_fact_contract.py
git commit -m "feat: add module fact contract (outputs/inputs) for chained exploits"
```

---

### Task 2: Catalogue fact validation

Wire the fact contract into the existing catalogue validation so CI/readiness flags bad `outputs`/`inputs`.

**Files:**
- Modify: `builder/catalogue_validation.py`
- Test: `tests/test_fact_contract.py` (add tests)

**Interfaces:**
- Consumes: `validate_module_facts`, `validate_catalogue_facts` from `builder/fact_contract.py`.
- Produces: `validate_catalogue()` now also returns fact errors.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fact_contract.py (append)
from builder.catalogue_validation import validate_catalogue


def test_catalogue_validation_reports_bad_inputs():
    a = mod("a", {"exploit": {"command": "x", "outputs": [{"trait": "ctf.a.shell"}]}})
    b = mod("b", {"exploit": {"command": "y", "inputs": ["ctf.a.nope"]}})
    report = validate_catalogue([a, b])
    assert any("unknown trait" in e for e in report["b"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fact_contract.py::test_catalogue_validation_reports_bad_inputs -v`
Expected: FAIL (assertion — validation not yet wired)

- [ ] **Step 3: Wire validation**

In `builder/catalogue_validation.py`, extend `validate_catalogue`:

```python
def validate_catalogue(modules: list[Module]) -> dict[str, list[str]]:
    from builder.fact_contract import validate_catalogue_facts
    known = {module.id for module in modules}
    result = {
        module.id: errors
        for module in modules
        if (errors := validate_module(module, known))
    }
    for module_id, fact_errors in validate_catalogue_facts(modules).items():
        result.setdefault(module_id, []).extend(fact_errors)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fact_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add builder/catalogue_validation.py tests/test_fact_contract.py
git commit -m "feat: validate module fact inputs/outputs in catalogue readiness"
```

---

### Task 3: Operation compiler

Compile a normalized operation plan into a resolved graph carrying module/ability metadata and the fact contract per node. Pure (no DB, no Caldera).

**Files:**
- Create: `builder/operation_compiler.py`
- Test: `tests/test_operation_compiler.py`

**Interfaces:**
- Consumes: `normalize_operation_plan` (from `builder/operation_plan`), `ability_facts` (from `builder/fact_contract`), `ability_uuid` (from `builder/caldera`), `Module`.
- Produces (imported by Tasks 6, 7, 9):
  - `CompiledNode` dataclass: `node_id: str`, `node_type: str`, `label: str`, `config: dict`, `module_id: str | None`, `phase: str | None`, `ability_id: str | None`, `command: str | None`, `tactic: str | None`, `input_traits: list[str]`, `output_specs: list[FactSpec]`
  - `CompiledPlan` dataclass: `nodes: dict[str, CompiledNode]`, `edges: list[dict]`, `trigger: dict`, `policy: dict`
  - `compile_operation(plan: dict, modules_by_id: dict[str, Module]) -> CompiledPlan`
  - `edge_activated(condition: str, result: str) -> bool`
  - `next_ready_nodes(nodes: dict[str, CompiledNode], edges: list[dict], completed: dict[str, str]) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_operation_compiler.py
from types import SimpleNamespace

from builder.operation_compiler import compile_operation, edge_activated, next_ready_nodes


def module(module_id, caldera):
    return SimpleNamespace(id=module_id, type="vulnerability", caldera=caldera)


WEAK_SSH = module("weak_ssh", {
    "tactic": "initial-access",
    "recon": {"command": "echo VULNERABLE user=svc"},
    "exploit": {"command": "su #{ctf.vuln.weak_ssh}", "outputs": [{"trait": "ctf.weak_ssh.shell", "marker": "FOOTHOLD"}]},
})
NOPASSWD = module("nopasswd_sudo", {
    "tactic": "privilege-escalation",
    "recon": {"command": "echo VULNERABLE"},
    "exploit": {"command": "sudo id", "inputs": ["ctf.weak_ssh.shell"]},
})


def plan():
    return {
        "version": 1,
        "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
                   "default_retries": 0, "default_retry_delay_seconds": 5},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual", "config": {}},
            {"id": "a", "type": "ability", "label": "Foothold",
             "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
            {"id": "b", "type": "ability", "label": "Privesc",
             "config": {"module_id": "nopasswd_sudo", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
            {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "a", "condition": "always"},
            {"id": "e2", "source": "a", "target": "b", "condition": "success"},
            {"id": "e3", "source": "b", "target": "finish", "condition": "always"},
        ],
    }


def test_compile_resolves_module_metadata_and_facts():
    compiled = compile_operation(plan(), {"weak_ssh": WEAK_SSH, "nopasswd_sudo": NOPASSWD})
    assert compiled.nodes["a"].module_id == "weak_ssh"
    assert compiled.nodes["a"].phase == "exploit"
    assert compiled.nodes["a"].command == "su #{ctf.vuln.weak_ssh}"
    assert compiled.nodes["a"].input_traits == ["ctf.vuln.weak_ssh"]
    assert [s.trait for s in compiled.nodes["a"].output_specs] == ["ctf.weak_ssh.shell"]
    assert compiled.nodes["b"].input_traits == ["ctf.weak_ssh.shell"]


def test_edge_activated_semantics():
    assert edge_activated("always", "failure")
    assert edge_activated("success", "success")
    assert not edge_activated("success", "failure")
    assert edge_activated("failure", "failure")
    assert edge_activated("failure", "skipped")   # skipped follows failure edge


def test_next_ready_nodes_sequential_chain():
    compiled = compile_operation(plan(), {"weak_ssh": WEAK_SSH, "nopasswd_sudo": NOPASSWD})
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {})) == {"a"}
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"a": "success"})) == {"b"}
    assert set(next_ready_nodes(compiled.nodes, compiled.edges, {"a": "failure"})) == {"finish"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the compiler**

```python
# builder/operation_compiler.py
from __future__ import annotations

from dataclasses import dataclass, field

from builder.fact_contract import FactSpec, ability_facts
from builder.caldera import ability_uuid
from builder.module_loader import Module
from builder.operation_plan import normalize_operation_plan


@dataclass
class CompiledNode:
    node_id: str
    node_type: str
    label: str
    config: dict
    module_id: str | None = None
    phase: str | None = None
    ability_id: str | None = None
    command: str | None = None
    tactic: str | None = None
    input_traits: list[str] = field(default_factory=list)
    output_specs: list[FactSpec] = field(default_factory=list)


@dataclass
class CompiledPlan:
    nodes: dict[str, CompiledNode] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    trigger: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)


def compile_operation(plan: dict, modules_by_id: dict[str, Module]) -> CompiledPlan:
    plan = normalize_operation_plan(plan)
    compiled = CompiledPlan(edges=plan["edges"], policy=plan["policy"])
    compiled.trigger = next((n for n in plan["nodes"] if n["type"].endswith("_trigger")), {})

    for node in plan["nodes"]:
        node_type = node["type"]
        config = node["config"]
        cnode = CompiledNode(
            node_id=node["id"], node_type=node_type, label=node["label"],
            config=config, tactic=None,
        )
        if node_type == "ability":
            module = modules_by_id[config["module_id"]]
            phase = config["ability"]
            facts = ability_facts(module, phase)
            cnode.module_id = module.id
            cnode.phase = phase
            cnode.ability_id = ability_uuid(module.id, phase)
            cnode.command = (module.caldera or {}).get(phase, {}).get("command")
            cnode.tactic = (module.caldera or {}).get("tactic")
            cnode.input_traits = list(facts.inputs)
            cnode.output_specs = list(facts.outputs)
        elif node_type == "objective":
            cnode.module_id = config["module_id"]
        compiled.nodes[node["id"]] = cnode
    return compiled


def edge_activated(condition: str, result: str) -> bool:
    if condition == "always":
        return True
    if condition == "success":
        return result == "success"
    if condition == "failure":
        return result in ("failure", "skipped")
    return False


def _satisfied(node: CompiledNode, incoming: list[dict], status: dict[str, str]) -> bool:
    activated = [e for e in incoming if e["source"] in status and edge_activated(e["condition"], status[e["source"]])]
    if node.node_type == "gate":
        mode = node.config.get("mode", "all")
        if mode == "all":
            return len(activated) == len(incoming) and all(e["source"] in status for e in incoming)
        return bool(activated)
    return bool(activated)


def next_ready_nodes(nodes: dict[str, CompiledNode], edges: list[dict], completed: dict[str, str]) -> list[str]:
    status: dict[str, str] = dict(completed)
    for node_id, node in nodes.items():
        if node.node_type.endswith("_trigger"):
            status.setdefault(node_id, "success")

    changed = True
    while changed:
        changed = False
        for node_id, node in nodes.items():
            if node_id in status:
                continue
            incoming = [e for e in edges if e["target"] == node_id]
            if not incoming:
                continue
            if not all(e["source"] in status for e in incoming):
                continue
            if not _satisfied(node, incoming, status):
                status[node_id] = "skipped"
                changed = True

    ready: list[str] = []
    for node_id, node in nodes.items():
        if node_id in status:
            continue
        incoming = [e for e in edges if e["target"] == node_id]
        if not incoming:
            continue
        if _satisfied(node, incoming, status):
            ready.append(node_id)
    return sorted(ready)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_operation_compiler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add builder/operation_compiler.py tests/test_operation_compiler.py
git commit -m "feat: compile operation plans into resolved run graphs"
```

---

### Task 4: Run/step models and migration

Add `OperationRun` and `OperationRunStep` models and the migration.

**Files:**
- Modify: `api/models.py` (add two classes after `EventOperation`)
- Create: `migrations/versions/0016_operation_runs.py`
- Test: `tests/test_operation_runs_model.py`

**Interfaces:**
- Consumes: `Base`, `utcnow`, SQLAlchemy `Mapped`/`mapped_column` from `api.models`.
- Produces (imported by Tasks 6, 7):
  - `OperationRun` with columns: `id`, `event_id`, `operation_id` (FK `event_operations.id`), `team_id` (FK `teams.id`, nullable), `status: str` default `"queued"`, `plan_snapshot: Text`, `fact_store: Text` default `"{}"`, `trigger: Text`, `started_at`, `finished_at`, `created_at`, `updated_at`.
  - `OperationRunStep` with columns: `id`, `run_id` (FK `operation_runs.id`), `node_id`, `node_type`, `status: str` default `"queued"`, `result: str` nullable, `output: Text`, `attempts: int` default 0, `caldera_operation_id: str` nullable, `started_at`, `finished_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_operation_runs_model.py
from api.models import OperationRun, OperationRunStep


def test_run_and_step_persist(db_session):
    run = OperationRun(event_id=1, operation_id=2, team_id=None, status="queued",
                       plan_snapshot="{}", fact_store="{}", trigger="{}")
    db_session.add(run)
    db_session.commit()
    step = OperationRunStep(run_id=run.id, node_id="a", node_type="ability", status="queued")
    db_session.add(step)
    db_session.commit()
    assert db_session.get(OperationRun, run.id).status == "queued"
    assert db_session.get(OperationRunStep, step.id).node_id == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_runs_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'OperationRun'`

- [ ] **Step 3: Add models**

Append to `api/models.py` after `EventOperation`:

```python
class OperationRun(Base):
    __tablename__ = "operation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    operation_id: Mapped[int] = mapped_column(ForeignKey("event_operations.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    plan_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    fact_store: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trigger: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.current_timestamp(), nullable=False
    )

    steps: Mapped[list["OperationRunStep"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class OperationRunStep(Base):
    __tablename__ = "operation_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("operation_runs.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=True)
    output: Mapped[str] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    caldera_operation_id: Mapped[str] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    run: Mapped["OperationRun"] = relationship(back_populates="steps")
```

- [ ] **Step 4: Add migration**

```python
# migrations/versions/0016_operation_runs.py
"""Add operation_runs and operation_run_steps tables.

Revision ID: 0016_operation_runs
Revises: 0015_module_repos
"""

from alembic import op

revision = "0016_operation_runs"
down_revision = "0015_module_repos"
branch_labels = None
depends_on = None


def upgrade():
    from api.database import Base
    import api.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("operation_run_steps")
    op.drop_table("operation_runs")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_operation_runs_model.py -v`
Expected: PASS (test fixture applies migrations via `Base.metadata.create_all`).

- [ ] **Step 6: Commit**

```bash
git add api/models.py migrations/versions/0016_operation_runs.py tests/test_operation_runs_model.py
git commit -m "feat: add OperationRun and OperationRunStep models"
```

---

### Task 5: Single-ability Caldera driver

Extend the Caldera client for strict single-agent execution and add single-ability adversaries to plugin generation, then wrap them in a driver service.

**Files:**
- Modify: `builder/caldera.py` (add `single_ability_adversary_id`, `build_single_ability_adversaries`, wire into `generate_caldera_event_export`)
- Modify: `api/services/caldera.py` (`create_operation` gains `allowed_agents`; add `get_adversary_by_id`)
- Create: `api/services/operation_driver.py`
- Test: `tests/test_operation_driver.py` (uses a fake client)

**Interfaces:**
- Consumes: `_adversary_uuid`, `_build_abilities`, `_write_plugin` (caldera.py); `CalderaClient.create_operation/get_operation`.
- Produces (imported by Task 6):
  - `single_ability_adversary_id(ability_id: str) -> str` (in `builder/caldera.py`)
  - `AbilityResult` dataclass: `status: int`, `output: str`, `finished: bool`
  - `class OperationDriver` with constructor `(caldera: CalderaClient)`, methods:
    - `async ensure_run_source(run_id: int) -> str` → source id `f"ctf-run-{run_id}"`
    - `async seed_run_facts(source_id: str, fact_store: dict[str, str]) -> None`
    - `async execute(self, ability_id: str, adversary_id: str, agent_paw: str, group: str, source_id: str, timeout_seconds: int) -> AbilityResult`
    - `async resolve_agent_paw(ip_address: str) -> str | None`

- [ ] **Step 1: Write the failing test (driver with a fake client)**

```python
# tests/test_operation_driver.py
from api.services.operation_driver import AbilityResult, OperationDriver


class FakeCaldera:
    def __init__(self):
        self.operations = []
        self.op_counter = 0

    async def ensure_source(self, source_id, name="ctf"):
        return None

    async def seed_facts(self, facts, source_id=None, name="ctf"):
        self.seeded = facts

    async def create_operation(self, name, adversary_id, planner_id, group,
                               source_id=None, auto_close=True, autonomous=True,
                               state=None, obfuscator="plain-text", jitter="2/8",
                               visibility=50, allowed_agents=None):
        self.op_counter += 1
        op = {"id": f"op-{self.op_counter}", "allowed_agents": allowed_agents}
        self.operations.append(op)
        return op

    async def get_operation(self, op_id, include_chain=False):
        return {"id": op_id, "state": "finished", "chain": [
            {"status": 0, "output": "VULNERABLE user=svc", "finish": "2026-08-18T00:00:00Z"},
        ]}

    async def get_agent_by_ip(self, ip):
        return {"paw": "abc123"}


async def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_driver_returns_result_and_targets_single_agent():
    fake = FakeCaldera()
    driver = OperationDriver(fake)
    result = _run(driver.execute("some-ability", "some-adversary", "abc123",
                                 "event-1", "ctf-run-1", 120))
    assert isinstance(result, AbilityResult)
    assert result.status == 0
    assert result.finished is True
    assert "VULNERABLE" in result.output
    assert fake.operations[0]["allowed_agents"] == ["abc123"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_driver.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add single-ability adversaries to plugin generation**

In `builder/caldera.py`, add after `_adversary_uuid`:

```python
def single_ability_adversary_id(ability_id: str) -> str:
    return _adversary_uuid(f"single_{ability_id}")


def build_single_ability_adversaries(abilities: list[dict]) -> list[dict]:
    profiles = []
    for a in abilities:
        profiles.append({
            "id": single_ability_adversary_id(a["id"]),
            "name": f"CTF Single: {a['name']}",
            "description": f"Single ability: {a['name']}",
            "objective": None,
            "abilities": [{"id": a["id"], "comment": a["name"]}],
        })
    return profiles
```

In `generate_caldera_event_export`, before `all_adversaries = legacy_adversaries + path_adversaries`, insert:

```python
    single_adversaries = build_single_ability_adversaries(abilities)
    all_adversaries = legacy_adversaries + path_adversaries + single_adversaries
```

(Also update the two assignment sites in `generate_caldera_export` and `generate_caldera_export_multi_path` so all export paths carry single-ability adversaries: `profiles = _build_adversary_profiles(...) + build_single_ability_adversaries(abilities)`.)

- [ ] **Step 4: Extend the Caldera client**

In `api/services/caldera.py`, add `allowed_agents` to `create_operation` signature and payload:

```python
    async def create_operation(self, name, adversary_id, planner_id, group,
                               source_id=CTF_SOURCE_ID, auto_close=True, autonomous=True,
                               state=None, obfuscator="plain-text", jitter="2/8",
                               visibility=50, allowed_agents=None):
        payload = {
            "name": name, "adversary": {"adversary_id": adversary_id},
            "planner": {"id": planner_id}, "source": {"id": source_id},
            "group": group, "auto_close": auto_close,
            "autonomous": 1 if autonomous else 0,
            "obfuscator": obfuscator, "jitter": jitter, "visibility": visibility,
        }
        if allowed_agents:
            payload["allowed_agents"] = allowed_agents
        if state:
            payload["state"] = state
        resp = await self._client.post("/api/v2/operations", json=payload)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 5: Write the driver service**

```python
# api/services/operation_driver.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from api.services.caldera import CalderaClient, get_caldera_api_key


@dataclass
class AbilityResult:
    status: int
    output: str
    finished: bool


class OperationDriver:
    def __init__(self, caldera: CalderaClient | None = None):
        self.caldera = caldera or CalderaClient(get_caldera_api_key())

    async def ensure_run_source(self, run_id: int) -> str:
        source_id = f"ctf-run-{run_id}"
        await self.caldera.ensure_source(source_id, name=f"ctf-run-{run_id}")
        return source_id

    async def seed_run_facts(self, source_id: str, fact_store: dict[str, str]) -> None:
        facts = [{"trait": trait, "value": value} for trait, value in fact_store.items()]
        if facts:
            await self.caldera.seed_facts(facts, source_id=source_id)

    async def resolve_agent_paw(self, ip_address: str) -> str | None:
        agent = await self.caldera.get_agent_by_ip(ip_address)
        return agent.get("paw") if agent else None

    async def execute(self, ability_id: str, adversary_id: str, agent_paw: str,
                      group: str, source_id: str, timeout_seconds: int) -> AbilityResult:
        planner = await self.caldera.get_planner_by_name("atomic")
        op = await self.caldera.create_operation(
            name=f"CTF step {ability_id[:8]}", adversary_id=adversary_id,
            planner_id=planner["id"], group=group, source_id=source_id,
            autonomous=True, state="running", allowed_agents=[agent_paw],
        )
        op_id = op["id"]
        deadline = time.monotonic() + timeout_seconds
        while True:
            detail = await self.caldera.get_operation(op_id, include_chain=True)
            if detail.get("state") in ("finished", "cleanup", "failed"):
                break
            if time.monotonic() > deadline:
                return AbilityResult(status=-1, output="timeout", finished=False)
            await asyncio.sleep(2)
        chain = detail.get("chain", [])
        link = chain[-1] if chain else {}
        return AbilityResult(
            status=link.get("status", -1),
            output=link.get("output", "") or "",
            finished=bool(link.get("finish")),
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_operation_driver.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add builder/caldera.py api/services/caldera.py api/services/operation_driver.py tests/test_operation_driver.py
git commit -m "feat: add single-ability Caldera driver for orchestrated sweeps"
```

---

### Task 6: Runner state machine

The orchestrator: a pure traversal helper plus an async run loop that drives nodes through the driver, maintaining the platform fact store and step states.

**Files:**
- Create: `api/services/operation_runner.py`
- Test: `tests/test_operation_runner.py` (state machine only, fake driver + in-memory step store)

**Interfaces:**
- Consumes: `CompiledPlan`, `CompiledNode`, `next_ready_nodes` (Task 3); `FactSpec`, `extract_facts`, `substitute_command` (Task 1); `AbilityResult`, `OperationDriver` (Task 5); `OperationRun`, `OperationRunStep` (Task 4).
- Produces (imported by Task 7):
  - `async def launch_run(run_id: int) -> None`
  - `def mark_interrupted_runs(db) -> None` (synchronous — called from the sync startup block)
  - `def decide_node_execution(node: CompiledNode, fact_store: dict[str, str]) -> NodeResult` — pure skip decision (testable without Caldera), returning `NodeResult(skipped: bool)`. Command substitution is NOT done here; the driver seeds the fact store into the per-run Caldera source and Caldera substitutes `#{trait}` natively.

- [ ] **Step 1: Write the failing test for the pure node execution decision**

```python
# tests/test_operation_runner.py
from types import SimpleNamespace

from api.services.operation_runner import decide_node_execution
from builder.operation_compiler import compile_operation


def _module(module_id, caldera):
    return SimpleNamespace(id=module_id, type="vulnerability", caldera=caldera)


MODULES = {
    "weak_ssh": _module("weak_ssh", {
        "tactic": "initial-access",
        "recon": {"command": "echo VULNERABLE user=svc"},
        "exploit": {"command": "su #{ctf.vuln.weak_ssh}",
                    "outputs": [{"trait": "ctf.weak_ssh.shell", "marker": "FOOTHOLD"}]},
    }),
    "nopasswd_sudo": _module("nopasswd_sudo", {
        "tactic": "privilege-escalation",
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {"command": "sudo id", "inputs": ["ctf.weak_ssh.shell"]},
    }),
}

PLAN = {
    "version": 1,
    "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
               "default_retries": 0, "default_retry_delay_seconds": 5},
    "nodes": [
        {"id": "trigger", "type": "manual_trigger", "label": "Manual", "config": {}},
        {"id": "a", "type": "ability", "label": "Foothold",
         "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
        {"id": "b", "type": "ability", "label": "Privesc",
         "config": {"module_id": "nopasswd_sudo", "ability": "exploit", "target_vm_id": "vm:hq/blue/web"}},
        {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
    ],
    "edges": [
        {"id": "e1", "source": "trigger", "target": "a", "condition": "always"},
        {"id": "e2", "source": "a", "target": "b", "condition": "success"},
        {"id": "e3", "source": "b", "target": "finish", "condition": "always"},
    ],
}


def test_missing_input_is_skipped():
    compiled = compile_operation(PLAN, MODULES)
    node = compiled.nodes["b"]  # inputs: ["ctf.weak_ssh.shell"]
    decision = decide_node_execution(node, {"ctf.vuln.weak_ssh": "svc"})
    assert decision.skipped is True


def test_present_inputs_do_not_skip():
    compiled = compile_operation(PLAN, MODULES)
    node = compiled.nodes["b"]
    decision = decide_node_execution(node, {"ctf.weak_ssh.shell": "svc", "ctf.vuln.weak_ssh": "svc"})
    assert decision.skipped is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the runner**

```python
# api/services/operation_runner.py
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from api.database import SessionLocal
from api.models import OperationRun, OperationRunStep, Site, VM, Zone, utcnow
from api.services.caldera import vm_source_facts
from api.services.operation_driver import OperationDriver
from builder.caldera import single_ability_adversary_id
from builder.fact_contract import extract_facts
from builder.operation_compiler import CompiledNode, CompiledPlan, next_ready_nodes


@dataclass
class NodeResult:
    skipped: bool = False


def decide_node_execution(node: CompiledNode, fact_store: dict[str, str]) -> NodeResult:
    if node.node_type != "ability":
        return NodeResult()
    if any(trait not in fact_store for trait in node.input_traits):
        return NodeResult(skipped=True)
    return NodeResult()


async def launch_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.query(OperationRun).filter(OperationRun.id == run_id).first()
        if not run or run.status in ("completed", "failed", "cancelled"):
            return
        # Compile the frozen plan snapshot.
        from builder.operation_compiler import compile_operation
        from builder.module_loader import load_all_modules
        modules_by_id = {m.id: m for m in load_all_modules()}
        compiled = compile_operation(json.loads(run.plan_snapshot), modules_by_id)

        fact_store = json.loads(run.fact_store or "{}")
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        db.commit()

        completed: dict[str, str] = {}
        driver = OperationDriver()
        async with driver.caldera:
            source_id = await driver.ensure_run_source(run_id)
            await driver.seed_run_facts(source_id, _platform_facts(db, run))

            while True:
                run = db.query(OperationRun).get(run_id)
                if run.status == "cancelled":
                    break
                ready = next_ready_nodes(compiled.nodes, compiled.edges, completed)
                if not ready:
                    break
                for node_id in ready:
                    node = compiled.nodes[node_id]
                    result = await _run_node(db, run, node, compiled, fact_store, driver, source_id)
                    completed[node_id] = result
                    fact_store = json.loads((db.query(OperationRun).get(run_id)).fact_store)
                if any(node_id not in completed for node_id in compiled.nodes):
                    if not ready:
                        break
        _finalize_run(db, run_id, completed, compiled)
    finally:
        db.close()


async def _run_node(db, run, node, compiled, fact_store, driver, source_id) -> str:
    step = db.query(OperationRunStep).filter_by(run_id=run.id, node_id=node.node_id).first()
    if not step:
        step = OperationRunStep(run_id=run.id, node_id=node.node_id, node_type=node.node_type)
        db.add(step)
    step.status = "running"
    step.started_at = utcnow()
    db.commit()

    if node.node_type in ("trigger", "target", "finish"):
        return _finish_step(db, step, "success")

    if node.node_type == "delay":
        await asyncio.sleep(int(node.config.get("seconds", 0)))
        return _finish_step(db, step, "success")

    if node.node_type == "objective":
        achieved = f"ctf.goal.{node.module_id}" in fact_store
        return _finish_step(db, step, "success" if achieved else "failure")

    decision = decide_node_execution(node, fact_store)
    if decision.skipped:
        step.output = "SKIPPED: missing prerequisite facts"
        return _finish_step(db, step, "skipped")

    vm = _resolve_target_vm(db, run, node)
    if vm is None or not vm.ip_address:
        step.output = "SKIPPED: target VM has no agent"
        return _finish_step(db, step, "failure")
    agent_paw = await driver.resolve_agent_paw(vm.ip_address)
    if agent_paw is None:
        step.output = "SKIPPED: no Caldera agent for target VM"
        return _finish_step(db, step, "failure")

    timeout_seconds = int(node.config.get("timeout_seconds", compiled.policy["default_timeout_seconds"]))
    ability_result = await driver.execute(
        node.ability_id, single_ability_adversary_id(node.ability_id), agent_paw,
        f"event-{run.event_id}", source_id, timeout_seconds,
    )
    step.attempts += 1
    step.output = (ability_result.output or "")[:2000]
    new_facts = extract_facts(ability_result.output or "", node.output_specs)
    run = db.query(OperationRun).get(run.id)
    store = json.loads(run.fact_store or "{}")
    store.update(new_facts)
    run.fact_store = json.dumps(store)
    result = "success" if (ability_result.finished and ability_result.status == 0) else "failure"
    _finish_step(db, step, result)
    await driver.seed_run_facts(source_id, store)
    return result


def _finish_step(db, step, result) -> str:
    step.status = result
    step.result = result
    step.finished_at = utcnow()
    db.commit()
    return result


def _resolve_target_vm(db, run, node):
    target = node.config.get("target_vm_id", "")
    parts = target.split("/")  # "vm:<site>/<zone>/<endpoint>"
    if len(parts) != 3:
        return None
    site_key = parts[0].split(":", 1)[1]
    zone_key = parts[1]
    endpoint_key = parts[2]
    query = (db.query(VM)
             .join(Site, VM.site_id == Site.id)
             .join(Zone, VM.zone_id == Zone.id)
             .filter(VM.event_id == run.event_id, Site.key == site_key,
                     Zone.key == zone_key, VM.vm_type == endpoint_key))
    if run.team_id is not None:
        query = query.filter(VM.team_id == run.team_id)
    return query.first()


def _platform_facts(db, run) -> dict[str, str]:
    facts: dict[str, str] = {}
    vms = db.query(VM).filter(VM.event_id == run.event_id)
    if run.team_id is not None:
        vms = vms.filter(VM.team_id == run.team_id)
    for vm in vms.all():
        for f in vm_source_facts(vm):
            facts[f["trait"]] = f["value"]
    return facts

def _finalize_run(db, run_id, completed, compiled):
    run = db.query(OperationRun).get(run_id)
    finish_node = next((n for n in compiled.nodes.values() if n.node_type == "finish"), None)
    run.status = "completed" if (finish_node and completed.get(finish_node.node_id) == "success") else "failed"
    run.finished_at = utcnow()
    db.commit()


def mark_interrupted_runs(db) -> None:
    runs = db.query(OperationRun).filter(OperationRun.status.in_(["running", "awaiting_approval"])).all()
    for run in runs:
        run.status = "failed"
    db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_operation_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/operation_runner.py tests/test_operation_runner.py
git commit -m "feat: add operation run state machine and orchestrator"
```

---

### Task 7: API endpoints and startup wiring

Expose launch/list/detail/approve/cancel and mark interrupted runs on startup.

**Files:**
- Modify: `api/routes/admin.py` (add endpoints after `preview_event_operation_plan`)
- Modify: `api/main.py` (call `mark_interrupted_runs` in startup near line 165-187)

**Interfaces:**
- Consumes: `launch_run` (Task 6), `OperationRun`/`OperationRunStep` (Task 4), `_event_operation`, `_operation_context`, `require_admin`.
- Produces: REST endpoints documented in the spec Section 4.

- [ ] **Step 1: Write the failing test**

The admin router in `api/routes/admin.py` carries the prefix `/admin/api`, so the JSON endpoints are served at `/admin/api/...`. The test uses a `client` fixture (not patched for auth) so an unauthenticated POST returns 403; model after `tests/test_event_operations_api.py` (in-memory SQLite via `StaticPool`, `app.include_router(router)`, `app.dependency_overrides[get_db] = override_db`, `TestClient`). For this test do NOT patch `require_admin`:

```python
# tests/test_operation_runs_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event
from api.routes.admin import router


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client


def test_run_endpoint_rejects_non_admin(client):
    resp = client.post("/admin/api/events/1/operations/1/run", json={})
    assert resp.status_code == 403
```

(Add `import pytest` at the top of the file.) In Step 5 add happy-path tests using `unittest.mock.patch("api.routes.admin.require_admin", return_value=...)` plus a seeded `Event`, `EventOperation`, and `Team`, following `tests/test_event_operations_api.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_runs_api.py -v`
Expected: FAIL (404/405 before admin check)

- [ ] **Step 3: Add endpoints**

In `api/routes/admin.py`, after `preview_event_operation_plan`:

```python
@router.post("/events/{event_id}/operations/{operation_id}/run")
async def run_event_operation(event_id: int, operation_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    event = db.query(Event).filter(Event.id == event_id).first()
    operation = _event_operation(db, event_id, operation_id)
    if not event or not operation:
        return JSONResponse({"error": "Operation not found"}, status_code=404)
    body = await request.json()
    from builder.operation_plan import validate_operation_plan
    infrastructure, module_plan, modules = _operation_context(event)
    plan = json.loads(operation.operation_plan)
    issues = validate_operation_plan(plan, infrastructure, module_plan, modules, event.time_limit_minutes)
    if issues:
        return JSONResponse({"error": "operation plan is invalid", "issues": issues}, status_code=422)

    team_ids = []
    if body.get("team_id") is not None:
        team = db.query(Team).filter(Team.id == body["team_id"], Team.event_id == event_id).first()
        if not team:
            return JSONResponse({"error": "Team not found"}, status_code=404)
        team_ids = [team.id]
    else:
        team_ids = [t.id for t in db.query(Team).filter(Team.event_id == event_id).all()]

    created = []
    for team_id in team_ids:
        run = OperationRun(event_id=event_id, operation_id=operation_id, team_id=team_id,
                           status="queued", plan_snapshot=operation.operation_plan,
                           fact_store="{}", trigger="{}")
        db.add(run)
        db.commit()
        db.refresh(run)
        created.append(run.id)
        asyncio.create_task(launch_run(run.id))
    return {"status": "started", "run_ids": created}


@router.get("/events/{event_id}/operations/{operation_id}/runs")
async def list_event_operation_runs(event_id: int, operation_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    runs = db.query(OperationRun).filter(OperationRun.operation_id == operation_id).all()
    return {"runs": [{"id": r.id, "status": r.status, "team_id": r.team_id,
                      "started_at": r.started_at.isoformat() if r.started_at else None,
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs]}


@router.get("/operation-runs/{run_id}")
async def get_operation_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    run = db.query(OperationRun).get(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    steps = db.query(OperationRunStep).filter(OperationRunStep.run_id == run_id).all()
    return {"id": run.id, "status": run.status, "team_id": run.team_id,
            "fact_store": json.loads(run.fact_store or "{}"),
            "steps": [{"node_id": s.node_id, "node_type": s.node_type, "status": s.status,
                       "result": s.result, "output": s.output, "attempts": s.attempts}
                      for s in steps]}


@router.post("/operation-runs/{run_id}/steps/{step_id}/approve")
async def approve_run_step(run_id: int, step_id: int, request: Request, db: Session = Depends(get_db)):
    return await _approve_reject_run_step(run_id, step_id, request, db, approve=True)


@router.post("/operation-runs/{run_id}/steps/{step_id}/reject")
async def reject_run_step(run_id: int, step_id: int, request: Request, db: Session = Depends(get_db)):
    return await _approve_reject_run_step(run_id, step_id, request, db, approve=False)


async def _approve_reject_run_step(run_id, step_id, request, db, approve):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    step = db.query(OperationRunStep).filter(OperationRunStep.id == step_id,
                                             OperationRunStep.run_id == run_id).first()
    if not step or step.status != "awaiting_approval":
        return JSONResponse({"error": "step not awaiting approval"}, status_code=409)
    step.status = "queued" if approve else "rejected"
    db.commit()
    return {"status": "approved" if approve else "rejected"}


@router.post("/operation-runs/{run_id}/cancel")
async def cancel_operation_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    run = db.query(OperationRun).get(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    run.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}
```

Ensure `asyncio` is imported at the top of `admin.py` (it already is, per the `asyncio.create_task` usages seen elsewhere).

- [ ] **Step 4: Wire startup recovery**

In `api/main.py`, in the startup block after the interrupted-provisioning handling (`interrupt_running_jobs(db)` around line 174), add:

```python
        from api.services.operation_runner import mark_interrupted_runs
        mark_interrupted_runs(db)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_operation_runs_api.py -v`
Expected: PASS (the 403 test; add happy-path tests with a `require_admin` patch + seeded EventOperation/Team as `tests/test_event_operations_api.py` does).

- [ ] **Step 6: Commit**

```bash
git add api/routes/admin.py api/main.py tests/test_operation_runs_api.py
git commit -m "feat: add operation run launch/detail/approve/cancel endpoints"
```

---

### Task 8: UI — Run action and run detail view

Add a Run button to the operation designer and a run detail view with per-step output, fact store, and approval controls.

**Files:**
- Modify: `frontend/static/event-operation.js` (add Run button + handler)
- Modify: `frontend/templates/event_operation.html` (toolbar button)
- Create: `frontend/templates/operation_run.html`
- Modify: `api/main.py` (HTML route for `/admin/events/{event_id}/operations/{operation_id}/runs/{run_id}`)
- Modify: `api/routes/admin.py` (or `main.py`) to serve the run page

**Interfaces:**
- Consumes: `GET /admin/api/events/{event_id}/operations/{operation_id}/runs` and `GET /admin/api/operation-runs/{run_id}` (Task 7).

- [ ] **Step 1: Add the Run button**

In `frontend/templates/event_operation.html`, in the operation toolbar (next to the existing Save button), add:

```html
<button id="operation-run" class="btn primary">Run</button>
```

In `frontend/static/event-operation.js`, wire it (fetch the run endpoint, then redirect to the run detail):

```javascript
document.getElementById('operation-run')?.addEventListener('click', async () => {
  const resp = await fetch(`/admin/api/events/${eventId}/operations/${operationId}/run`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}),
  });
  const data = await resp.json();
  if (!resp.ok) { announce(data.error || 'Failed to start run'); return; }
  const runId = data.run_ids?.[0];
  if (runId) window.location = `/admin/events/${eventId}/operations/${operationId}/runs/${runId}`;
});
```

(Use the existing `eventId`/`operationId` variables and `announce()` helper already present in that file.)

- [ ] **Step 2: Add the run detail page**

Create `frontend/templates/operation_run.html` with a status header, a per-step list (node_id → status/result/output), a fact-store table, and approve/reject buttons for steps with `status === "awaiting_approval"`. Poll `GET /admin/api/operation-runs/{run_id}` every 4s and re-render (mirror the polling pattern in the provisioning progress bar). Approve/reject call `POST /admin/api/operation-runs/{run_id}/steps/{step_id}/approve` / `.../reject`.

- [ ] **Step 3: Add the HTML route**

In `api/main.py`, add:

```python
@app.get("/admin/events/{event_id}/operations/{operation_id}/runs/{run_id}", response_class=HTMLResponse)
async def operation_run_page(event_id: int, operation_id: int, run_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    if not user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "operation_run.html",
        {"user": user, "event_id": event_id, "operation_id": operation_id, "run_id": run_id})
```

- [ ] **Step 4: Commit**

```bash
git add frontend/templates/event_operation.html frontend/static/event-operation.js frontend/templates/operation_run.html api/main.py
git commit -m "feat: add operation run UI (Run action + run detail view)"
```

---

### Task 9: Showcase default event and chain module facts

Seed the draft "Operation Chaining Demo" event with the authored chain, and add the fact contract to the three chain modules.

**Files:**
- Modify: `api/main.py` (startup seed near the existing default-event creation, line 177-187)
- Modify: `modules/vulns/weak_ssh_credentials/*.yaml`, `modules/vulns/nopasswd_sudo/*.yaml`, `modules/payloads/malicious_cron_beacon/*.yaml` (add `outputs`/`inputs`)
- Test: `tests/test_showcase_event.py`

**Interfaces:**
- Consumes: `Event`, `EventOperation`, `Team` models; `empty_module_plan`/`normalize_module_plan`, `default_infrastructure`/`normalize_infrastructure`, `compile_operation` (Task 3).
- Produces: a draft event named "Operation Chaining Demo" with one `EventOperation` whose plan is the RCE → privesc → implant chain.

- [ ] **Step 1: Add module fact contracts**

In `weak_ssh_credentials` exploit section, add (the module already declares `recon` with a `ctf_extract` parser):

```yaml
  exploit:
    description: "Authenticate using the discovered weak account to establish a foothold"
    outputs:
      - trait: ctf.weak_ssh_credentials.shell
        marker: FOOTHOLD
    command: |
      USER="#{ctf.vuln.weak_ssh_credentials}"
      su -c "id && echo FOOTHOLD" "$USER"
```

In `nopasswd_sudo` exploit section, add:

```yaml
  exploit:
    description: "Escalate to root via the NOPASSWD sudo entry"
    inputs:
      - ctf.weak_ssh_credentials.shell
    outputs:
      - trait: ctf.nopasswd_sudo.root
        marker: ROOT_SHELL
    command: |
      sudo -n id 2>/dev/null | grep -q "uid=0" && echo "ROOT_SHELL" || echo "SKIPPED: no root shell"
```

In `malicious_cron_beacon` exploit section, add:

```yaml
  exploit:
    description: "Install a persistent beacon using the obtained root shell"
    inputs:
      - ctf.nopasswd_sudo.root
    command: |
      [ -n "#{ctf.nopasswd_sudo.root}" ] || { echo "SKIPPED: root shell not obtained"; exit 0; }
      echo "beacon installed" && echo "IMPLANT"
```

(Adjust `outputs`/`inputs` to the exact existing YAML structure; preserve all existing fields.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_showcase_event.py
def test_showcase_event_seeded_idempotently(db_session):
    from api.main import seed_showcase_event  # extracted helper
    seed_showcase_event(db_session)
    seed_showcase_event(db_session)
    from api.models import Event, EventOperation
    events = db_session.query(Event).filter(Event.name == "Operation Chaining Demo").all()
    assert len(events) == 1
    op = db_session.query(EventOperation).filter(EventOperation.event_id == events[0].id).first()
    assert op is not None
    assert op.name == "RCE → Privilege Escalation → Implant"
```

- [ ] **Step 3: Extract and implement the seed helper**

In `api/main.py`, add a module-level function `seed_showcase_event(db)` and call it in the startup block after the default-event creation. The helper:

```python
def seed_showcase_event(db) -> None:
    from api.models import Event, EventOperation
    if db.query(Event).filter(Event.name == "Operation Chaining Demo").first():
        return
    infrastructure = {"version": 1, "sites": [{"key": "demo", "name": "Demo", "zones": [{
        "key": "site", "name": "Demo Site", "team": "blue", "endpoints": [{
            "key": "box", "name": "Demo Box", "base_type": "ubuntu_24_server",
        }],
    }]}]}
    module_plan = {"version": 1, "assignments": {"vm:demo/site/box": {
        "mode": "manual_only",
        "pinned_module_ids": ["weak_ssh_credentials", "nopasswd_sudo", "malicious_cron_beacon"],
        "resolved_module_ids": ["weak_ssh_credentials", "nopasswd_sudo", "malicious_cron_beacon"],
    }}}
    plan = {
        "version": 1,
        "policy": {"time_limit_minutes": 60, "max_concurrency": 1, "default_timeout_seconds": 120,
                   "default_retries": 0, "default_retry_delay_seconds": 5, "instructor_approval": False},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual Trigger", "config": {}},
            {"id": "foothold", "type": "ability", "label": "Foothold (weak SSH)",
             "config": {"module_id": "weak_ssh_credentials", "ability": "exploit", "target_vm_id": "vm:demo/site/box"}},
            {"id": "privesc", "type": "ability", "label": "Privilege Escalation (NOPASSWD sudo)",
             "config": {"module_id": "nopasswd_sudo", "ability": "exploit", "target_vm_id": "vm:demo/site/box"}},
            {"id": "implant", "type": "ability", "label": "Implant (cron beacon)",
             "config": {"module_id": "malicious_cron_beacon", "ability": "exploit", "target_vm_id": "vm:demo/site/box"}},
            {"id": "finish", "type": "finish", "label": "Finish", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "foothold", "condition": "always"},
            {"id": "e2", "source": "foothold", "target": "privesc", "condition": "success"},
            {"id": "e3", "source": "privesc", "target": "implant", "condition": "success"},
            {"id": "e4", "source": "implant", "target": "finish", "condition": "always"},
        ],
    }
    event = Event(name="Operation Chaining Demo", status="draft",
                  quota='{"vulnerability":{"easy":2,"medium":1,"hard":0}}',
                  infrastructure=json.dumps(infrastructure),
                  module_plan=json.dumps(module_plan))
    db.add(event)
    db.flush()
    db.add(EventOperation(event_id=event.id, name="RCE → Privilege Escalation → Implant",
                          description="Chained sweep: foothold → privesc → implant",
                          position=0, operation_plan=json.dumps(plan)))
    db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_showcase_event.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/main.py modules/vulns/weak_ssh_credentials modules/vulns/nopasswd_sudo modules/payloads/malicious_cron_beacon tests/test_showcase_event.py
git commit -m "feat: seed chaining demo event and wire chain module facts"
```

---

### Task 10: Integration test, docs, and full-suite verification

Add the end-to-end sweep integration test and update docs.

**Files:**
- Create: `tests/test_operation_sweep_integration.py`
- Modify: `MODULE_GUIDE.md`, `CLAUDE.md` (document `outputs`/`inputs`, the run endpoints, and the showcase event)

**Interfaces:**
- Consumes: everything from Tasks 1-9.

- [ ] **Step 1: Write the integration test (skipped when Caldera is unavailable)**

```python
# tests/test_operation_sweep_integration.py
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CALDERA_INTERNAL_URL") or os.environ.get("CTF_SKIP_CALDERA_TESTS"),
    reason="requires a running Caldera",
)


def test_single_ability_driver_round_trip():
    import asyncio
    from api.services.caldera import CalderaClient, get_caldera_api_key
    from api.services.operation_driver import OperationDriver

    async def run():
        async with CalderaClient(get_caldera_api_key()) as caldera:
            driver = OperationDriver(caldera)
            source_id = await driver.ensure_run_source(999999)
            result = await driver.execute("does-not-exist", "does-not-exist", "paw",
                                          "event-0", source_id, 30)
            return result
    result = asyncio.run(run())
    assert result.finished is False or result.status != 0
```

- [ ] **Step 2: Run the full suite**

Run: `docker compose --profile test build tests && docker compose --profile test run --rm tests`
Expected: all tests pass (including existing attack-tree, caldera-builder, operation-plan suites).

- [ ] **Step 3: Document the feature**

In `MODULE_GUIDE.md`, add an "Outputs and Inputs (Exploit Chaining)" section documenting the `outputs`/`inputs` fields, module-scoped trait naming (`ctf.<module_id>.<name>`), marker/pattern/group semantics, and the auto-derived defaults. In `CLAUDE.md`, add a short "Operation Chaining Execution" section covering the run endpoints, the `OperationRun`/`OperationRunStep` models, and the seeded "Operation Chaining Demo" event.

- [ ] **Step 4: Commit**

```bash
git add tests/test_operation_sweep_integration.py MODULE_GUIDE.md CLAUDE.md
git commit -m "docs: document operation chaining and add integration coverage"
```

---

## Self-Review Notes (author, done pre-handoff)

- **Spec coverage:** Section 1 (architecture) → Tasks 5-6; Section 2 (fact contract) → Tasks 1-2; Section 3 (state machine) → Tasks 3, 6; Section 4 (persistence/API/UI) → Tasks 4, 7, 8; Section 5 (showcase) → Task 9; Section 6 (error/concurrency/scoring) → Task 6 + startup wiring in Task 7; Section 7 (testing/rollout) → Task 10.
- **Type consistency:** `CompiledNode`/`CompiledPlan`/`next_ready_nodes`/`edge_activated` defined in Task 3 and consumed identically in Task 6; `FactSpec`/`ability_facts`/`substitute_command`/`extract_facts` defined in Task 1 and consumed in Tasks 3/6; `AbilityResult`/`OperationDriver` defined in Task 5 and consumed in Task 6; `single_ability_adversary_id` defined in Task 5 and consumed in Task 6's `adversary_id_for`.
- **Known implementation-time verification (from spec):** confirm Caldera v5 accepts `allowed_agents`; the Task 10 integration test exercises this path.
