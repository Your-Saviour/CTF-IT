# Ability Facts Visualization Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the broken "ability input/output facts" UI (added by a previous agent in uncommitted work) so both the operation planner and the Caldera dashboard correctly surface each ability's `inputs`/`outputs` fact contract.

**Architecture:** A single shared serializer (`fact_summary`) in `builder/fact_contract.py` produces JSON-ready `{inputs, outputs}` for a `(module, phase)` pair. Two endpoints — one for the design-time event plan (`api/routes/admin.py`), one for the running Caldera operation (`api/routes/caldera_ops.py`) — both call it. The dashboard consumes a map keyed by Caldera `ability_id`; the planner consumes a list keyed by plan `node_id` and renders facts inside the existing ability inspector.

**Tech Stack:** Python/FastAPI + SQLAlchemy (API), Jinja2 templates, vanilla ES-module JS (`frontend/static/*.js`), Node's built-in test runner (`node --test`) for frontend unit tests, pytest for backend.

**Spec:**
- `docs/specs/2026-08-17-module-caldera-facts-adoption.md` (fact contract: `outputs`/`inputs`, trait naming, marker/pattern semantics)
- `docs/specs/2026-08-18-operation-chaining-execution.md` (the execution layer that consumes these facts)

## Global Constraints

- Fact traits are module-scoped: `ctf.<module_id>.<name>`; auto-derived recon fact is `ctf.vuln.<module_id>`; auto-derived goal fact is `ctf.goal.<goal_id>`.
- `ability_facts(module, phase)` returns `AbilityFacts(inputs: list[str], outputs: list[FactSpec])`; `FactSpec` has `trait`, `marker`, `pattern`, `group`.
- Phase values are exactly `"recon"` and `"exploit"` (never "both"/"any").
- Plan nodes are dicts `{id, type, label, x, y, disabled, config}`; ability nodes carry `config.module_id` and `config.ability` (the phase), **not** top-level `module_id`/`phase`/`ability_id`.
- Backend tests run via `docker compose --profile test run --rm tests` (pytest only — the test image has no Node). Frontend tests run via `node --test tests/*.test.mjs` on the host (Node treats `.mjs` and imports `.js` as ES modules).
- Commit style: conventional commits (`fix:`, `feat:`, `test:`), matching the existing `git log`.
- The frontend is a browser ES module; a single syntax error in `event-operation.js` breaks the entire operation planner page. Verify with `node --check` against a `.mjs` copy (plain `node --check` on a `.js` file silently treats it as CommonJS and misses ESM errors).

---

### Task 1: Add the shared `fact_summary` helper

**Files:**
- Modify: `builder/fact_contract.py` (append helper after `ability_facts`, around line 112)
- Test: `tests/test_fact_contract.py` (append test)

**Interfaces:**
- Produces: `fact_summary(module: Module, phase: str) -> dict` returning `{"inputs": list[str], "outputs": [{"trait": str, "marker": str, "pattern": str}]}`. Consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fact_contract.py` (reuses the existing `mod()` helper already defined at the top of that file):

```python
def test_fact_summary_serializes_inputs_and_outputs():
    from builder.fact_contract import fact_summary
    m = mod("nopasswd_sudo", {
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {
            "command": "sudo id",
            "inputs": ["ctf.weak_ssh.shell"],
            "outputs": [{"trait": "ctf.nopasswd_sudo.root", "marker": "ROOT_SHELL"}],
        },
    })
    assert fact_summary(m, "exploit") == {
        "inputs": ["ctf.weak_ssh.shell"],
        "outputs": [{"trait": "ctf.nopasswd_sudo.root", "marker": "ROOT_SHELL", "pattern": ""}],
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose --profile test run --rm tests tests/test_fact_contract.py::test_fact_summary_serializes_inputs_and_outputs -v`
Expected: FAIL with `ImportError: cannot import name 'fact_summary'`.

- [ ] **Step 3: Implement the helper**

Append to `builder/fact_contract.py` (after `ability_facts`, before `substitute_command`):

```python
def fact_summary(module: Module, phase: str) -> dict:
    """Serialize a module/phase ability's fact contract for the UI.

    Returns JSON-ready `{"inputs": [...], "outputs": [{"trait", "marker",
    "pattern"}, ...]}` so API routes don't leak the `FactSpec` dataclass.
    """
    facts = ability_facts(module, phase)
    return {
        "inputs": list(facts.inputs),
        "outputs": [
            {"trait": o.trait, "marker": o.marker, "pattern": o.pattern}
            for o in facts.outputs
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose --profile test run --rm tests tests/test_fact_contract.py -v`
Expected: PASS (all `test_fact_contract` tests still pass).

- [ ] **Step 5: Commit**

```bash
git add builder/fact_contract.py tests/test_fact_contract.py
git commit -m "feat: add fact_summary serializer for ability fact contracts"
```

---

### Task 2: Fix the event-plan ability-facts endpoint (`api/routes/admin.py`)

**Files:**
- Modify: `api/routes/admin.py` (imports at lines 17-19, `get_operation_run` indentation at 1084-1086, replace `get_event_operation_ability_facts` at 1089-1143)
- Test: `tests/test_ability_facts_api.py` (create)

**Interfaces:**
- Consumes: `fact_summary` (Task 1).
- Produces: `GET /admin/api/events/{event_id}/operations/{operation_id}/ability-facts` → `{"fact_data": [{"node_id", "module_id", "module_name", "phase", "inputs", "outputs"}, ...]}` keyed by plan node id. Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ability_facts_api.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.database import Base, get_db
from api.models import Event, EventOperation, User
from api.routes.admin import router


def _plan_with_ability():
    return {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "label": "Manual", "x": 0, "y": 0, "config": {}},
            {"id": "a1", "type": "ability", "label": "Exploit", "x": 100, "y": 0,
             "config": {"module_id": "weak_ssh", "ability": "exploit", "target_vm_id": "vm:web"}},
            {"id": "finish", "type": "finish", "label": "Finish", "x": 200, "y": 0, "config": {}},
        ],
        "edges": [],
    }


def _fake_module(module_id="weak_ssh"):
    return SimpleNamespace(
        id=module_id, name="Weak SSH", type="vulnerability",
        caldera={"recon": {"command": "echo VULNERABLE"}, "exploit": {"command": "su svc"}},
        stage=None, references=[], tags=[], requires=[],
        prerequisites=[], conflicts=[], verification=None,
    )


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def override_db():
        s = sessions()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        c.sessions = sessions
        yield c


def test_ability_facts_reads_module_and_phase_from_node_config(client):
    db = client.sessions()
    event = Event(name="Exercise", quota="{}", status="open")
    db.add(event); db.commit(); db.refresh(event)
    op = EventOperation(event_id=event.id, name="Phase 1", position=0,
                        operation_plan=json.dumps(_plan_with_ability()))
    db.add(op); db.commit(); db.refresh(op)
    db.close()

    with patch("api.routes.admin.require_admin", return_value=User(is_admin=True)), \
         patch("api.routes.admin.load_all_modules", return_value=[_fake_module()]):
        resp = client.get(f"/admin/api/events/{event.id}/operations/{op.id}/ability-facts")

    assert resp.status_code == 200
    facts = resp.json()["fact_data"]
    assert len(facts) == 1
    assert facts[0]["node_id"] == "a1"
    assert facts[0]["module_id"] == "weak_ssh"
    assert facts[0]["module_name"] == "Weak SSH"
    assert facts[0]["phase"] == "exploit"
    assert facts[0]["inputs"] == ["ctf.vuln.weak_ssh"]
    assert facts[0]["outputs"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose --profile test run --rm tests tests/test_ability_facts_api.py -v`
Expected: FAIL — the current endpoint returns `fact_data` entries with empty `module_id`/`phase` (reads nonexistent top-level node fields), so `len(facts) == 1` and `facts[0]["module_id"] == "weak_ssh"` fail.

- [ ] **Step 3: Fix the imports**

In `api/routes/admin.py`, change the import block (lines 17-19) to:

```python
from api.models import AccountToken, AdminAudit, Event, EventOperation, OpnsenseImage, OperationRun, OperationRunStep, PlatformSettings, Team, User, VerificationAttempt, VM, VMModule, utcnow
from builder.fact_contract import fact_summary
from builder.module_loader import load_all_modules
from api.routes.auth import _token_digest, get_current_user
from api.services.operation_runner import launch_run
```

(Removes the unused `CalderaClient, get_caldera_api_key` import and the now-unused `ability_facts` import; adds `fact_summary` and a top-level `load_all_modules` so the endpoint is patchable in tests.)

- [ ] **Step 4: Replace the endpoint**

Replace the current `get_event_operation_ability_facts` (lines 1089-1143) with:

```python
@router.get("/events/{event_id}/operations/{operation_id}/ability-facts")
async def get_event_operation_ability_facts(event_id: int, operation_id: int, request: Request, db: Session = Depends(get_db)):
    """Return structured input/output facts for each ability in the event operation plan."""
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    operation = db.query(EventOperation).filter(
        EventOperation.event_id == event_id, EventOperation.id == operation_id
    ).first()
    if not operation:
        return JSONResponse({"error": "Operation not found"}, status_code=404)

    plan = json.loads(operation.operation_plan or "{}")
    modules_by_id = {m.id: m for m in load_all_modules()}

    fact_data = []
    for node in plan.get("nodes", []):
        if node.get("type") != "ability":
            continue
        config = node.get("config") or {}
        module_id = config.get("module_id")
        phase = config.get("ability")
        module = modules_by_id.get(module_id)
        if not module or phase not in ("recon", "exploit"):
            continue
        fact_data.append({
            "node_id": node["id"],
            "module_id": module_id,
            "module_name": module.name,
            "phase": phase,
            **fact_summary(module, phase),
        })

    return {"fact_data": fact_data}
```

- [ ] **Step 5: Restore the `get_operation_run` indentation**

In `api/routes/admin.py` lines 1084-1086, restore the dict continuation to proper indentation:

```python
            "steps": [{"id": s.id, "node_id": s.node_id, "node_type": s.node_type, "status": s.status,
                       "result": s.result, "output": s.output, "attempts": s.attempts}
                      for s in steps]}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose --profile test run --rm tests tests/test_ability_facts_api.py tests/test_operation_runs_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/routes/admin.py tests/test_ability_facts_api.py
git commit -m "fix: read ability facts from plan node config, not top-level fields"
```

---

### Task 3: Fix the Caldera operation ability-facts endpoint (`api/routes/caldera_ops.py`)

**Files:**
- Modify: `api/routes/caldera_ops.py` (imports at lines 9-25, replace `get_operation_ability_facts` at 415-513)
- Test: `tests/test_ability_facts_api.py` (append test)

**Interfaces:**
- Consumes: `fact_summary` (Task 1).
- Produces: `GET /admin/api/caldera/operations/{op_id}/ability-facts` → `{"fact_data": {ability_id: {"module_id", "module_name", "phase", "inputs", "outputs"}, ...}}` — a **map** keyed by Caldera `ability_id`. Consumed by Task 4.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ability_facts_api.py` (add `from api.routes import caldera_ops as caldera_ops_module` and reuse `_fake_module`):

```python
from api.routes import caldera_ops as caldera_ops_module


class FakeCaldera:
    def __init__(self, op):
        self._op = op

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get_operation(self, op_id, include_chain=False):
        return self._op


@pytest.fixture
def caldera_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(caldera_ops_module.router)

    def override_db():
        s = sessions()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_caldera_ability_facts_returns_map_keyed_by_ability_id(caldera_client):
    fake_op = {"chain": [{"ability": {"ability_id": "uuid-1"}}]}
    with patch("api.routes.caldera_ops.require_admin", return_value=User(is_admin=True)), \
         patch("api.routes.caldera_ops._make_client", return_value=FakeCaldera(fake_op)), \
         patch("api.routes.caldera_ops.load_all_modules", return_value=[_fake_module()]), \
         patch("api.routes.caldera_ops.build_ability_uuid_map", return_value={
             "uuid-1": {"module_id": "weak_ssh", "module_name": "Weak SSH", "phase": "exploit"},
         }):
        resp = caldera_client.get("/admin/api/caldera/operations/op-1/ability-facts")

    assert resp.status_code == 200
    facts = resp.json()["fact_data"]
    assert facts == {
        "uuid-1": {
            "module_id": "weak_ssh", "module_name": "Weak SSH", "phase": "exploit",
            "inputs": ["ctf.vuln.weak_ssh"], "outputs": [],
        }
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose --profile test run --rm tests tests/test_ability_facts_api.py -v`
Expected: FAIL — the current endpoint returns `fact_data` as a **list** (and calls `list_agents`, which `FakeCaldera` lacks), so `facts == {...}` fails.

- [ ] **Step 3: Fix the imports**

In `api/routes/caldera_ops.py`, replace the import block (lines 9-25) with:

```python
from api.database import get_db
from api.models import Event, Team, VM, VMModule
from api.routes.admin import require_admin
from api.services.caldera import CalderaClient, get_caldera_api_key
from builder.caldera import build_ability_uuid_map
from builder.fact_contract import fact_summary
from builder.module_loader import load_all_modules
```

(Removes the duplicate `ability_facts` import and the `# noqa` re-import at lines 22-24.)

- [ ] **Step 4: Replace the endpoint**

Replace `get_operation_ability_facts` (lines 415-513) with:

```python
@router.get("/operations/{op_id}/ability-facts")
async def get_operation_ability_facts(op_id: str, request: Request, db: Session = Depends(get_db)):
    """Return structured input/output facts for each ability in the operation chain."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with _make_client() as caldera:
        try:
            op = await caldera.get_operation(op_id, include_chain=True)
        except Exception as e:
            return JSONResponse({"error": f"Caldera unavailable: {e}"}, status_code=502)

    modules = load_all_modules()
    uuid_to_module = build_ability_uuid_map(modules)
    modules_by_id = {m.id: m for m in modules}

    fact_data = {}
    for link in op.get("chain", []):
        ability_id = (link.get("ability") or {}).get("ability_id", "")
        info = uuid_to_module.get(ability_id)
        if not info:
            continue
        module = modules_by_id.get(info["module_id"])
        if not module:
            continue
        fact_data[ability_id] = {
            "module_id": info["module_id"],
            "module_name": info["module_name"],
            "phase": info["phase"],
            **fact_summary(module, info["phase"]),
        }

    return {"fact_data": fact_data}
```

(Removes the dead `list_agents`/`paw_to_vm`/`annotated_chain` work that was never returned.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose --profile test run --rm tests tests/test_ability_facts_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/caldera_ops.py tests/test_ability_facts_api.py
git commit -m "fix: return Caldera ability facts as an ability_id map"
```

---

### Task 4: Fix the Caldera dashboard `factDataMap` contract (`frontend/templates/caldera_dashboard.html`)

**Files:**
- Modify: `frontend/templates/caldera_dashboard.html` (lines 447 and 514)

**Interfaces:**
- Consumes: the map returned by Task 3's endpoint (`fact_data` keyed by `ability_id`).

- [ ] **Step 1: Change the initializer and assignment**

At line 447 change:

```js
    var factDataMap = [];
```

to:

```js
    var factDataMap = {};
```

At line 514 change:

```js
                        factDataMap = factsResp.fact_data || [];
```

to:

```js
                        factDataMap = factsResp.fact_data || {};
```

(The inline `renderAbilityFacts` at lines 721-780 already looks up `factDataMap[c.ability_id]`; once it's a map instead of an array, the `inputs`/`outputs` columns render correctly.)

- [ ] **Step 2: Verify no dashboard tests regress**

Run: `docker compose --profile test run --rm tests tests/test_caldera_lifecycle.py tests/test_event_dashboard.py -v`
Expected: PASS (template still renders; only inline JS data-type changed).

- [ ] **Step 3: Commit**

```bash
git add frontend/templates/caldera_dashboard.html
git commit -m "fix: index Caldera ability facts map by ability_id"
```

---

### Task 5: Redo the planner-side facts UI (`frontend/static/*.js`)

**Files:**
- Modify: `frontend/static/event-operation-ability-details.js` (add `renderAbilityFacts` export)
- Modify: `frontend/static/event-operation.js` (remove broken functions, wire up facts into the inspector)
- Test: `tests/event-operation-ability-details.test.mjs` (append tests)

**Interfaces:**
- Consumes: `fact_data` list from Task 2 (keyed by `node_id`).
- Produces: `renderAbilityFacts(facts) -> string` (pure, returns HTML or `''`).

- [ ] **Step 1: Write the failing JS test**

Append to `tests/event-operation-ability-details.test.mjs`:

```js
import {
  abilityCommand,
  findAbilityDetails,
  renderAbilityDetails,
  renderAbilityFacts,
} from '../frontend/static/event-operation-ability-details.js';

test('renders fact inputs and outputs with markers', () => {
  const html = renderAbilityFacts({
    inputs: ['ctf.vuln.weak_ssh'],
    outputs: [{ trait: 'ctf.weak_ssh.shell', marker: 'VULNERABLE', pattern: 'user=(\\S+)' }],
  });
  assert.match(html, /ctf\.vuln\.weak_ssh/);
  assert.match(html, /ctf\.weak_ssh\.shell/);
  assert.match(html, /VULNERABLE/);
});

test('returns empty string when no facts are present', () => {
  assert.equal(renderAbilityFacts({ inputs: [], outputs: [] }), '');
  assert.equal(renderAbilityFacts(null), '');
  assert.equal(renderAbilityFacts(undefined), '');
});
```

(Note: the existing `import` statement at the top of that file already imports `abilityCommand`, `findAbilityDetails`, `renderAbilityDetails`; extend it to also import `renderAbilityFacts`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/event-operation-ability-details.test.mjs`
Expected: FAIL with `SyntaxError: The requested module ... does not provide an export named 'renderAbilityFacts'`.

- [ ] **Step 3: Implement `renderAbilityFacts`**

Append to `frontend/static/event-operation-ability-details.js`:

```js
export function renderAbilityFacts(facts){
  const inputs=Array.isArray(facts?.inputs)?facts.inputs:[];
  const outputs=Array.isArray(facts?.outputs)?facts.outputs:[];
  if(!inputs.length&&!outputs.length)return '';
  const inputBlock=inputs.length
    ?`<div><h4>Inputs</h4><ul class="ability-fact-list">${inputs.map(t=>`<li><code>${esc(t)}</code></li>`).join('')}</ul></div>`
    :'<div><h4>Inputs</h4><p class="ability-command-empty">None</p></div>';
  const outputBlock=outputs.length
    ?`<div><h4>Outputs</h4><ul class="ability-fact-list">${outputs.map(o=>`<li><code>${esc(o.trait)}</code>${o.marker?` <span class="op-state-badge op-state-other">${esc(o.marker)}</span>`:''}</li>`).join('')}</ul></div>`
    :'<div><h4>Outputs</h4><p class="ability-command-empty">None</p></div>';
  return `<section class="ability-facts"><h3>Input / Output Facts</h3>${inputBlock}${outputBlock}</section>`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/event-operation-ability-details.test.mjs`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Fix `event-operation.js`**

In `frontend/static/event-operation.js`:

(a) Update the import (line 7) to include the new export and bump the cache-buster:

```js
import {abilityCommand, renderAbilityDetails, renderAbilityFacts} from './event-operation-ability-details.js?v=2';
```

(b) Delete the broken additions: `fetchAbilityFacts` (line 27), the broken `renderAbilityFacts` (line 30), `extractAbilityInfo` (lines 32-42), `renderAbilityInfo` (lines 44-74), and the two trailing `try { ... }catch(e){}` blocks (lines 156-160).

(c) Add a facts map next to the other `let` declarations (after line 18):

```js
let abilityFacts={};
```

(d) Add the loader after the `api(...)` helper (line 24):

```js
async function loadAbilityFacts(){
  try{
    const resp=await fetch(`/admin/api/events/${eventId}/operations/${operationId}/ability-facts`,{headers:{'Content-Type':'application/json'}});
    if(!resp.ok)return;
    const data=await resp.json();
    abilityFacts=Object.fromEntries((data.fact_data||[]).map(f=>[f.node_id,f]));
    if(selectedNodes.size||selectedEdge)renderInspector();
  }catch(error){console.error('Failed to load ability facts:',error)}
}
```

(e) In `renderAbilityDialog` (line 115), append facts to the expanded dialog:

```js
content.innerHTML=renderAbilityDetails(node,catalogue,{expanded:true})+renderAbilityFacts(abilityFacts[node.id]);
```

(f) In `renderInspector` (line 122), append facts to the Details tab:

```js
html=abilityTabs()+(inspectorTab==='details'?renderAbilityDetails(node,catalogue,{expanded:false})+renderAbilityFacts(abilityFacts[node.id]):nodeSettings(node));
```

(g) After the `api('')` load (line 154, inside the `.then(...)` after `renderAll()`), trigger the facts fetch:

```js
loadAbilityFacts();
```

- [ ] **Step 6: Verify the module parses as ESM**

Run: `cp frontend/static/event-operation.js /tmp/event-operation.mjs && node --check /tmp/event-operation.mjs; echo "exit: $?"`
Expected: exit `0` (no `Missing } in template expression`). This is the check that catches the original bug — do **not** rely on `node --check frontend/static/event-operation.js`, which silently treats the file as CommonJS.

- [ ] **Step 7: Run the JS tests**

Run: `node --test tests/event-operation-ability-details.test.mjs tests/event-operation-state.test.mjs`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/static/event-operation.js frontend/static/event-operation-ability-details.js tests/event-operation-ability-details.test.mjs
git commit -m "fix: render ability facts in the operation planner inspector"
```

---

### Task 6: Full-suite verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `docker compose --profile test build tests && docker compose --profile test run --rm tests`
Expected: `603 passed, 3 skipped` (or higher — now includes the new tests).

- [ ] **Step 2: Run the full frontend suite**

Run: `node --test tests/*.test.mjs`
Expected: all pass (no failures).

- [ ] **Step 3: Confirm no uncommitted junk remains**

Run: `git status --porcelain` and `git diff --stat`
Expected: only the six files intentionally touched (`builder/fact_contract.py`, `api/routes/admin.py`, `api/routes/caldera_ops.py`, `frontend/templates/caldera_dashboard.html`, `frontend/static/event-operation.js`, `frontend/static/event-operation-ability-details.js`) plus the two test files (`tests/test_ability_facts_api.py`, `tests/test_fact_contract.py`, `tests/event-operation-ability-details.test.mjs`).
