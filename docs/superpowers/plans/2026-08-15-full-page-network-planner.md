# Full-Page Event Network Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing event Plan page into a full-page diagram editor for canonical multi-site GameNet infrastructure, with individual VM nodes, durable layout, immediate validation, legacy compatibility, and read-only post-provisioning behavior.

**Architecture:** Keep `Event.infrastructure` as the provisioning contract, normalize legacy count-based endpoints at the boundary, and store visual coordinates separately in `Event.infrastructure_layout`. Split the browser implementation into a pure state/validation module and a D3 canvas/controller module; save infrastructure and layout atomically through the existing event API with `updated_at` optimistic concurrency.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, Jinja2, vanilla JavaScript ES modules, D3.js v7, CSS, pytest, optional Playwright browser acceptance tests.

## Global Constraints

- The diagram defines one canonical topology repeated for every event team.
- Each new endpoint record represents exactly one VM per team; legacy `count` records remain readable and provisionable.
- Network links are derived from VPN gateway → site firewall → zone → VM; arbitrary links, routes, ports, and firewall rules are out of scope.
- Infrastructure and layout are editable only while the event status is `draft`.
- Canvas position data must never influence provisioning behavior.
- Immediate browser validation is advisory; server validation remains authoritative for save, preview, and start.
- Run Python tests only through the disposable Docker test service described in `CLAUDE.md`.

---

### Task 1: Normalize Individual and Legacy Endpoint Shapes

**Files:**
- Create: `builder/infrastructure_planner.py`
- Modify: `builder/infrastructure_validation.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Produces: `default_infrastructure() -> dict`
- Produces: `normalize_infrastructure(value: dict) -> dict`, returning a deep copy whose endpoints are individual records with `count` removed and `name` populated.
- Produces: `endpoint_instances(endpoint: dict) -> list[dict]`, used by compatibility-sensitive provisioning and preview paths.
- Existing `validate_infrastructure(infrastructure, valid_base_ids, valid_regions=None, *, team_count=1, live_vpcs_by_region=None) -> list[str]` accepts both legacy and individual endpoint records.

- [ ] **Step 1: Add failing normalization and validation tests**

```python
def test_legacy_endpoint_groups_expand_without_mutating_input():
    legacy = deepcopy(INFRASTRUCTURE)
    expanded = normalize_infrastructure(legacy)
    endpoints = expanded["sites"][0]["zones"][0]["endpoints"]
    assert [(row["key"], row["name"]) for row in endpoints] == [
        ("workstation_1", "Workstation 1"),
        ("workstation_2", "Workstation 2"),
    ]
    assert legacy["sites"][0]["zones"][0]["endpoints"][0]["count"] == 2
    assert all("count" not in row for row in endpoints)


def test_individual_endpoint_requires_name_and_rejects_count():
    value = normalize_infrastructure(INFRASTRUCTURE)
    del value["sites"][0]["zones"][0]["endpoints"][0]["name"]
    assert "sites[0].zones[0].endpoints[0].name is required" in validate_infrastructure(value, BASES)
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py -k 'legacy_endpoint_groups_expand or individual_endpoint_requires' -v`

Expected: FAIL because `builder.infrastructure_planner` and individual endpoint validation do not exist.

- [ ] **Step 3: Implement the planner-domain normalization helpers**

```python
def endpoint_instances(endpoint: dict) -> list[dict]:
    count = endpoint.get("count")
    if count is None:
        return [deepcopy(endpoint)]
    stem = endpoint["key"]
    return [
        {**{key: deepcopy(value) for key, value in endpoint.items() if key != "count"},
         "key": f"{stem}_{index}", "name": f"{_humanize(stem)} {index}"}
        for index in range(1, count + 1)
    ]


def normalize_infrastructure(value: dict) -> dict:
    result = deepcopy(value)
    for site in result.get("sites", []):
        for zone in site.get("zones", []):
            used: set[str] = set()
            normalized = []
            for endpoint in zone.get("endpoints", []):
                for instance in endpoint_instances(endpoint):
                    instance["key"] = _next_free_key(instance["key"], used)
                    instance.setdefault("name", _humanize(instance["key"]))
                    used.add(instance["key"])
                    normalized.append(instance)
            zone["endpoints"] = normalized
    return result
```

Define the current starter topology once in `default_infrastructure()` and return a deep copy on every call.

- [ ] **Step 4: Update infrastructure validation and summaries**

Treat `count` as the legacy discriminator: validate it as a positive integer and count its instances; otherwise require `name` and count the endpoint as one. Keep all existing key/base/plan/address/VPC validation and calculate zone capacity with `sum(len(endpoint_instances(row)) for row in endpoints)`.

- [ ] **Step 5: Run all GameNet validation tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py -k 'infrastructure or hostname' -v`

Expected: PASS, including unchanged legacy fixtures.

- [ ] **Step 6: Commit the normalization boundary**

```bash
git add builder/infrastructure_planner.py builder/infrastructure_validation.py tests/test_gamenet.py
git commit -m "feat: normalize individual network endpoints"
```

---

### Task 2: Persist Layout and Event Revision State

**Files:**
- Create: `migrations/versions/0010_event_network_planner.py`
- Modify: `api/models.py`
- Modify: `api/routes/admin.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Produces: `Event.infrastructure_layout: str | None`
- Produces: `Event.updated_at: datetime`
- Produces: `validate_infrastructure_layout(layout: dict | None, infrastructure: dict) -> list[str]`
- Event read responses add `infrastructure_layout` and ISO-8601 `updated_at`.

- [ ] **Step 1: Add failing model and layout-schema tests**

```python
def test_layout_accepts_known_stable_node_ids():
    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    layout = {"version": 1, "nodes": {
        "gateway": {"x": 10, "y": 20},
        "site:head_office": {"x": 100.5, "y": 80},
        "vm:head_office/corporate/workstation_1": {"x": 220, "y": 300},
    }}
    assert validate_infrastructure_layout(layout, infrastructure) == []


def test_layout_rejects_unknown_ids_non_finite_coordinates_and_oversize_payload():
    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    errors = validate_infrastructure_layout(
        {"version": 1, "nodes": {"vm:unknown": {"x": float("inf"), "y": 0}}},
        infrastructure,
    )
    assert any("unknown node id" in error for error in errors)
    assert any("finite" in error for error in errors)
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py -k 'layout_' -v`

Expected: FAIL because layout persistence and validation do not exist.

- [ ] **Step 3: Add the Alembic migration and model columns**

```python
def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")}
    with op.batch_alter_table("events") as batch:
        if "infrastructure_layout" not in columns:
            batch.add_column(sa.Column("infrastructure_layout", sa.Text(), nullable=True))
        if "updated_at" not in columns:
            batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute(sa.text("UPDATE events SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"))
    with op.batch_alter_table("events") as batch:
        batch.alter_column("updated_at", nullable=False)
```

Map the fields on `Event`, using `default=utcnow` and `onupdate=utcnow` for `updated_at`. Leave downgrade non-destructive, matching this repository's migration policy.

- [ ] **Step 4: Implement stable-node enumeration and layout validation**

Add `infrastructure_node_ids(infrastructure) -> set[str]` and layout validation to `builder/infrastructure_planner.py`. Enforce version `1`, a dictionary of nodes, numeric finite `x`/`y`, known stable IDs, and a serialized size limit of 256 KiB. Return path-addressed error strings.

- [ ] **Step 5: Expose layout and revision fields in event reads**

Add this data to both list and detail event responses:

```python
"infrastructure_layout": json.loads(event.infrastructure_layout) if event.infrastructure_layout else None,
"updated_at": event.updated_at.isoformat(),
```

- [ ] **Step 6: Run migration and API regression tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_deploy_compose.py -v`

Expected: PASS with clean-install and existing-database upgrades.

- [ ] **Step 7: Commit persistence support**

```bash
git add migrations/versions/0010_event_network_planner.py api/models.py api/routes/admin.py builder/infrastructure_planner.py tests/test_gamenet.py
git commit -m "feat: persist event planner layout"
```

---

### Task 3: Make Provisioning and Preview Consume Individual Endpoints

**Files:**
- Modify: `api/services/gamenet_provisioning.py`
- Modify: `api/routes/admin.py`
- Modify: `api/services/gamenet.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Consumes: `endpoint_instances(endpoint) -> list[dict]` from Task 1.
- Preserves: legacy hostname shape for count groups (`gamenet-e1-t1/head-office/corporate/workstation/1`, normalized to the existing dash-safe hostname).
- Produces: individual hostname shape (`gamenet-e1-t1/head-office/corporate/workstation-1`, normalized to the existing dash-safe hostname) with no synthetic second ordinal.

- [ ] **Step 1: Add failing preview and provisioning tests for mixed shapes**

```python
def test_placeholders_materialize_one_vm_per_individual_endpoint(db_session):
    infrastructure = normalize_infrastructure(INFRASTRUCTURE)
    event, team = _event_with_team(db_session, infrastructure)
    allocate_event_networks(db_session, event, [team], infrastructure)
    placeholders = ensure_vm_placeholders(db_session, event, infrastructure)
    endpoint_vms = [vm for vm in placeholders if vm.role == "blue_endpoint"]
    assert [vm.vm_type for vm in endpoint_vms] == ["workstation_1", "workstation_2"]
    assert len({vm.hostname for vm in endpoint_vms}) == 2


def test_plan_preview_counts_individual_and_legacy_endpoints(monkeypatch, db_session):
    event = Event(name="GameNet", quota="{}", infrastructure=json.dumps(INFRASTRUCTURE))
    db_session.add(event)
    db_session.flush()
    db_session.add(Team(name="One", event_id=event.id))
    db_session.commit()
    monkeypatch.delenv("VULTR_API_KEY", raising=False)
    monkeypatch.setattr("api.routes.admin.require_admin", lambda *_args, **_kwargs: User(is_admin=True))
    legacy = asyncio.run(plan_preview(event.id, PlanPreviewRequest(), MagicMock(), db_session))
    individual = asyncio.run(plan_preview(
        event.id,
        PlanPreviewRequest(infrastructure=normalize_infrastructure(INFRASTRUCTURE)),
        MagicMock(),
        db_session,
    ))
    assert legacy["summary"]["total_vms"] == individual["summary"]["total_vms"] == 4
    assert legacy["summary"]["endpoints"] == individual["summary"]["endpoints"] == 2
    assert len(legacy["topology"]["nodes"]) == len(individual["topology"]["nodes"])
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py -k 'individual_endpoint or individual_and_legacy' -v`

Expected: FAIL where production code indexes `endpoint["count"]`.

- [ ] **Step 3: Replace nested count loops at every provisioning boundary**

Use `endpoint_instances(endpoint)` in `ensure_vm_placeholders`, endpoint creation, certification prerequisite collection, and endpoint base enumeration. For a legacy record, retain the existing group key plus ordinal hostname; for an individual record, use its unique endpoint key once. Keep address allocation sequential from host 10.

- [ ] **Step 4: Update plan preview machine definitions**

Build one machine definition per normalized endpoint:

```python
for endpoint in endpoint_instances(raw_endpoint):
    machine_types.append({
        "type_key": f"{site['key']}_{zone['key']}_{endpoint['key']}",
        "role": "attacker" if zone["team"] == "red" else "target",
        "count": 1,
        "spec": endpoint,
        "region": site["region"],
        "hostname": lambda team, _index, endpoint_key=endpoint["key"]:
            gamenet_hostname(event_id, team.id, site_key, zone_key, endpoint_key),
    })
```

- [ ] **Step 5: Run GameNet and plan-preview tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_event_plan_template.py -v`

Expected: PASS for legacy and individual documents with identical resource totals.

- [ ] **Step 6: Commit runtime compatibility**

```bash
git add api/services/gamenet_provisioning.py api/services/gamenet.py api/routes/admin.py tests/test_gamenet.py
git commit -m "feat: provision individual planned VMs"
```

---

### Task 4: Add Atomic Planner Save and Conflict Protection

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `frontend/static/admin-events.js`
- Modify: `frontend/templates/admin_resource.html`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Planner save request: `PUT /admin/api/events/{id}` with `{infrastructure, infrastructure_layout, expected_updated_at}`.
- Conflict response: HTTP `409` with `{error: "event draft has changed", current_updated_at: "2026-08-15T10:30:00+00:00"}`.
- Successful response: `{status: "updated", updated_at: "2026-08-15T10:31:00+00:00"}`.
- Consumes: `default_infrastructure()`, `normalize_infrastructure()`, and `validate_infrastructure_layout()`.

- [ ] **Step 1: Add failing API tests for atomic save, stale writes, and lifecycle lock**

```python
def test_planner_save_updates_infrastructure_and_layout_atomically(client, draft_event, admin_cookie):
    response = client.put(f"/admin/api/events/{draft_event.id}", cookies=admin_cookie, json={
        "infrastructure": normalize_infrastructure(INFRASTRUCTURE),
        "infrastructure_layout": {"version": 1, "nodes": {"gateway": {"x": 1, "y": 2}}},
        "expected_updated_at": draft_event.updated_at.isoformat(),
    })
    assert response.status_code == 200
    assert response.json()["updated_at"]


def test_planner_save_rejects_stale_revision_without_partial_update(client, draft_event, admin_cookie, db_session):
    original = draft_event.infrastructure
    response = client.put(f"/admin/api/events/{draft_event.id}", cookies=admin_cookie, json={
        "infrastructure": normalize_infrastructure(INFRASTRUCTURE),
        "infrastructure_layout": {"version": 1, "nodes": {"gateway": {"x": 1, "y": 2}}},
        "expected_updated_at": "2000-01-01T00:00:00+00:00",
    })
    db_session.refresh(draft_event)
    assert response.status_code == 409
    assert draft_event.infrastructure == original
    assert draft_event.infrastructure_layout is None


def test_non_draft_planner_save_rejects_layout_only_change(client, open_event, admin_cookie):
    response = client.put(f"/admin/api/events/{open_event.id}", cookies=admin_cookie, json={
        "infrastructure_layout": {"version": 1, "nodes": {"gateway": {"x": 1, "y": 2}}},
        "expected_updated_at": open_event.updated_at.isoformat(),
    })
    assert response.status_code == 409
    assert "cannot be edited" in response.json()["error"]
```

- [ ] **Step 2: Run the focused API tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py -k 'planner_save' -v`

Expected: FAIL because layout updates and revision checks are not handled.

- [ ] **Step 3: Implement atomic save semantics**

Parse both documents before assigning either field. Compare timezone-normalized `expected_updated_at` to the current event revision. Validate normalized infrastructure and layout together; on success assign both, set `event.updated_at = utcnow()`, commit once, and return the new token. Preserve the existing draft-only rule for either field.

- [ ] **Step 4: Centralize starter infrastructure and remove drawer JSON editing**

On event creation, use `default_infrastructure()` when the request omits `infrastructure`. Remove the GameNet JSON field and rail from `admin_resource.html`; remove `DEFAULT_INFRASTRUCTURE`, `renderRail`, and infrastructure serialization from `admin-events.js`. Add a planner link for an existing event and keep metadata/quota editing unchanged.

- [ ] **Step 5: Run event CRUD and GameNet tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_event_lifecycle.py tests/test_auth_security.py -v`

Expected: PASS; event creation receives the shared backend starter plan and stale saves cannot overwrite drafts.

- [ ] **Step 6: Commit the planner API boundary**

```bash
git add api/routes/admin.py frontend/static/admin-events.js frontend/templates/admin_resource.html tests/test_gamenet.py
git commit -m "feat: add atomic event planner saves"
```

---

### Task 5: Build the Planner Page Shell and State Model

**Files:**
- Replace: `frontend/templates/event_plan.html`
- Create: `frontend/static/event-planner.css`
- Create: `frontend/static/event-planner-state.js`
- Create: `frontend/static/event-planner.js`
- Modify: `api/main.py`
- Replace: `tests/test_event_plan_template.py`

**Interfaces:**
- Template globals: `EVENT_ID`, `EVENT_STATUS`, and `READ_ONLY` emitted as JSON-safe values.
- State module produces `createPlannerStore(initial)`, `normalizeClientInfrastructure(value)`, `validateClientInfrastructure(value, catalogues)`, `stableNodeId(node)`, and immutable actions for add/update/delete/select.
- Controller consumes the store and owns API calls, dirty/saving/error state, navigation guards, and DOM rendering.

- [ ] **Step 1: Add failing template-structure tests**

```python
def test_plan_page_loads_full_page_planner_assets():
    source = TEMPLATE.read_text()
    assert 'id="planner-outline"' in source
    assert 'id="planner-canvas"' in source
    assert 'id="planner-inspector"' in source
    assert 'id="planner-validation"' in source
    assert 'src="/static/event-planner.js' in source
    assert 'href="/static/event-planner.css' in source


def test_planner_assets_avoid_inline_event_handlers():
    assert "onclick=" not in TEMPLATE.read_text()
```

- [ ] **Step 2: Run template tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -v`

Expected: FAIL against the old 1,000-line preview template.

- [ ] **Step 3: Replace the template with semantic full-page regions**

Create a toolbar with Back, save state, Save Draft, Validate, Preview per Team, Reset Layout, and Start Event; add left outline/add controls, central `<svg id="planner-canvas">`, right inspector, validation summary, Advanced JSON dialog, preview dialog, tooltip, confirmation dialog, and accessible live regions. Load D3 v7 and `event-planner.js` as a module.

- [ ] **Step 4: Add the pure planner state model**

Represent selection as a stable node ID and infrastructure as normalized JSON. Implement explicit actions:

```javascript
export function addVm(state, siteKey, zoneKey, vm) {
  return updateZone(state, siteKey, zoneKey, zone => ({
    ...zone,
    endpoints: [...zone.endpoints, structuredClone(vm)],
  }));
}

export function updateNode(state, nodeId, patch) {
  return mapNode(state, nodeId, node => ({...node, ...structuredClone(patch)}));
}

export function deleteNode(state, nodeId) {
  const parentId = parentNodeId(nodeId);
  return {...removeNodeAndDescendants(state, nodeId), selectedNodeId: parentId};
}

export function validateClientInfrastructure(value, catalogues) {
  return collectInfrastructureErrors(value, catalogues).map(error => ({
    ...error,
    nodeId: nodeIdForPath(value, error.path),
  }));
}
```

Deleting the selected node selects its parent. Adding a site also adds its required firewall object. There is no delete-firewall action.

- [ ] **Step 5: Implement controller bootstrap and save-state behavior**

Fetch the event, `/admin/api/base-types`, and `/admin/api/vultr/plans`; normalize legacy endpoints only in memory, initialize clean state, and render the three regions. Track `clean`, `dirty`, `saving`, `saved`, and `failed`; install `beforeunload` only while dirty; send both infrastructure and layout with `expected_updated_at`; preserve edits on any failed request and handle `409` with a reload prompt. If either catalogue fails, keep the saved diagram visible, disable fields that depend on that catalogue, and show a Retry catalogues action that repeats only the failed request.

- [ ] **Step 6: Add the dedicated full-page visual system**

Implement the approved dark network-planning layout in `event-planner.css`, including responsive collapse below 900px, visible focus states, reduced-motion support, non-color-only status icons, and a minimum 44px target for primary controls.

- [ ] **Step 7: Run template and route tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_event_dashboard.py -v`

Expected: PASS; the existing `/admin/events/{id}/plan` route renders the new shell and includes event status/read-only state.

- [ ] **Step 8: Commit the planner shell**

```bash
git add frontend/templates/event_plan.html frontend/static/event-planner.css frontend/static/event-planner-state.js frontend/static/event-planner.js api/main.py tests/test_event_plan_template.py
git commit -m "feat: add full-page network planner shell"
```

---

### Task 6: Implement Outline, Inspector, Validation, and Advanced JSON

**Files:**
- Modify: `frontend/static/event-planner-state.js`
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes state actions from Task 5.
- Produces controller renderers `renderOutline()`, `renderInspector()`, `renderValidation()`, and `renderAdvancedJson()`.
- Inspector input paths match backend validation paths (`sites[0].zones[1].endpoints[0].name`) for error mapping.

- [ ] **Step 1: Add failing asset-contract tests**

```python
def test_planner_script_exposes_all_editor_surfaces():
    source = SCRIPT.read_text()
    for symbol in ("renderOutline", "renderInspector", "renderValidation", "applyAdvancedJson"):
        assert f"function {symbol}" in source or f"const {symbol}" in source
    assert "beforeunload" in source
    assert "expected_updated_at" in source
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -k 'editor_surfaces' -v`

Expected: FAIL until the four surfaces are implemented.

- [ ] **Step 3: Implement outline navigation and context-sensitive add actions**

Render gateway, sites, firewalls, zones, and VMs as a nested tree with buttons carrying stable IDs. Disable Add Zone without a site selection and Add VM without a zone selection; explain why through adjacent help text. New names generate slug keys once, and subsequent display-name edits do not modify keys.

- [ ] **Step 4: Implement inspector forms and destructive confirmations**

Render the exact fields from the design for gateway/site/firewall/zone/VM nodes. Populate base-type options from `/admin/api/base-types`; keep subnet read-only. Confirm zone deletion with VM count and site deletion with firewall/zone/VM totals before dispatching the cascade action.

- [ ] **Step 5: Implement immediate validation and server error mapping**

Validate on every state change, render a summary with focusable links to affected nodes, add an error badge to outline/canvas nodes, and bind inspector errors using the same path. Disable Save, Preview, and Start while errors exist; retain Validate as a focus action that moves to the first error.

- [ ] **Step 6: Implement two-way Advanced JSON editing**

Opening Advanced serializes the current infrastructure with two-space indentation. Apply parses into a temporary value, normalizes and validates it, and swaps state only on success. Syntax or schema failures stay inside the dialog and never alter the current diagram. Read-only events show copyable JSON without Apply.

- [ ] **Step 7: Run frontend contract and backend validation tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_gamenet.py -k 'planner or infrastructure' -v`

Expected: PASS for all editor surfaces and matching validation rules.

- [ ] **Step 8: Commit structured editing**

```bash
git add frontend/static/event-planner-state.js frontend/static/event-planner.js frontend/static/event-planner.css tests/test_event_plan_template.py
git commit -m "feat: add network planner editing controls"
```

---

### Task 7: Implement the D3 Canvas and Durable Layout

**Files:**
- Create: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `tests/test_event_plan_template.py`

**Interfaces:**
- Produces: `createPlannerCanvas(svg, callbacks)` with `render(graph, layout)`, `fit()`, `resetLayout()`, `focusNode(id)`, and `destroy()`.
- Emits: `callbacks.onSelect(nodeId)` and `callbacks.onLayoutChange(layout)`.
- Consumes: stable IDs and `{version: 1, nodes: {id: {x, y}}}` layout from earlier tasks.

- [ ] **Step 1: Add failing canvas contract tests**

```python
def test_canvas_module_contains_required_layout_contract():
    source = CANVAS.read_text()
    for token in ("createPlannerCanvas", "onLayoutChange", "resetLayout", "focusNode", "d3.zoom"):
        assert token in source
    assert "data-node-id" in source
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -k 'canvas_module' -v`

Expected: FAIL because the canvas module does not exist.

- [ ] **Step 3: Build deterministic topology projection and layout**

Project infrastructure into typed nodes, containment boxes, and derived links. Default layout places the gateway on the left, sites in columns, firewalls at each site's ingress, zones within sites, and VMs within zones. Use saved coordinates when present and default only missing nodes; ignore stale IDs.

- [ ] **Step 4: Add canvas interactions**

Use D3 zoom/pan and drag. Click or keyboard activation selects a node; dragging writes finite coordinates through `onLayoutChange`; fit-to-view frames all nodes; Reset Layout computes defaults and emits a clean version-1 layout. Render labels, node type/status icons, site/zone boundaries, tooltips, selected state, and validation badges.

- [ ] **Step 5: Connect canvas and store without feedback loops**

The controller passes new graph/layout state to `render`; canvas layout changes mark the store dirty but do not rebuild infrastructure. Outline/inspector selection calls `focusNode`; canvas selection rerenders outline/inspector. Throttle drag layout updates with `requestAnimationFrame` and persist final coordinates on drag end. When a site, zone, or VM key changes, remap that node's saved coordinate and all descendant stable IDs to the new key path before pruning stale layout IDs.

- [ ] **Step 6: Add responsive and accessibility behavior**

Provide SVG titles/labels, keyboard node traversal in structural order, Enter/Space selection, Escape to return focus to the outline, and non-color-only role/error markers. On narrow screens, switch between Outline, Canvas, and Inspector tabs without destroying canvas state.

- [ ] **Step 7: Run static planner tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py -v`

Expected: PASS for asset loading, no inline handlers, required canvas API, and accessibility hooks.

- [ ] **Step 8: Commit the interactive canvas**

```bash
git add frontend/static/event-planner-canvas.js frontend/static/event-planner.js frontend/static/event-planner.css tests/test_event_plan_template.py
git commit -m "feat: add interactive network planning canvas"
```

---

### Task 8: Integrate Preview, Start, and Read-Only Lifecycle

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/templates/event_plan.html`
- Modify: `tests/test_gamenet.py`
- Create: `tests/test_event_planner_browser.py`

**Interfaces:**
- Existing `POST /admin/api/events/{id}/plan-preview` continues to accept `{quota?, infrastructure?}` and receives the current valid unsaved infrastructure from the planner.
- Existing `POST /admin/api/events/{id}/start` remains the only start mutation.
- The planner is read-only whenever `event.status != "draft"`.

- [ ] **Step 1: Add failing lifecycle and unsaved-preview API tests**

```python
def test_plan_preview_uses_unsaved_individual_infrastructure(client, draft_event, admin_cookie):
    candidate = normalize_infrastructure(INFRASTRUCTURE)
    candidate["sites"][0]["zones"][0]["endpoints"].append({
        "key": "server_1", "name": "Server 1",
        "base_type": "ubuntu_24_server", "default_plan": "vc2-1c-1gb",
    })
    response = client.post(f"/admin/api/events/{draft_event.id}/plan-preview",
                           cookies=admin_cookie, json={"infrastructure": candidate})
    assert response.status_code == 200
    assert response.json()["summary"]["endpoints"] == 3


def test_plan_page_marks_non_draft_event_read_only(client, open_event, admin_cookie):
    response = client.get(f"/admin/events/{open_event.id}/plan", cookies=admin_cookie)
    assert '"read_only": true' in response.text.lower()
```

- [ ] **Step 2: Run focused lifecycle tests and confirm they fail**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_event_plan_template.py -k 'unsaved_individual or read_only' -v`

Expected: FAIL until the new page state and preview projection are integrated.

- [ ] **Step 3: Implement the per-team preview dialog**

Post current valid infrastructure without saving. Render summary totals, cost, address plan, warnings, VM/module assignments, and attack-path counts. Add a team selector that relabels the canonical projected nodes for one event team; do not create or persist runtime Site/Zone/VM rows.

- [ ] **Step 4: Integrate save-before-start semantics**

Start is disabled while dirty. When clean and valid, call the existing start endpoint after confirmation; display prerequisite errors (no teams, missing active OPNsense image, provider capacity) inline without losing the planner state; redirect to the event dashboard only after a successful start response.

- [ ] **Step 5: Enforce and render read-only mode**

Pass status/read-only into the page bootstrap. Hide add/delete/save/reset/apply controls for non-draft events, disable inspector fields, and keep outline selection, pan/zoom/fit, preview, and copyable JSON. Retain server-side 409 enforcement for direct API attempts.

- [ ] **Step 6: Add opt-in Playwright acceptance coverage**

Seed an admin, draft event, and teams. In `tests/test_event_planner_browser.py`, log in, open Plan, add a second site, add red/blue zones and individual VMs, edit inspector fields, drag a node, save, reload, and assert structure/position restoration. Also assert invalid data disables save/preview/start and that an open event exposes no editing controls.

- [ ] **Step 7: Run planner API and browser tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_event_plan_template.py -v`

Expected: PASS.

Run (opt-in): `docker compose --profile test run --rm -e RUN_BROWSER_E2E=true tests pytest tests/test_event_planner_browser.py -v`

Expected: PASS with Chromium available in the test image; otherwise document the environment skip and run it in the browser-enabled CI job.

- [ ] **Step 8: Commit lifecycle integration**

```bash
git add api/routes/admin.py frontend/static/event-planner.js frontend/templates/event_plan.html tests/test_gamenet.py tests/test_event_planner_browser.py
git commit -m "feat: integrate planner preview and lifecycle"
```

---

### Task 9: Final Regression, Documentation, and Cleanup

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `.gitignore`
- Test: `tests/`

**Interfaces:**
- Documents the individual endpoint schema, planner route, legacy compatibility, layout separation, and draft-only editing rule.

- [ ] **Step 1: Update operator and developer documentation**

Document the full-page Plan workflow, the one-VM-per-endpoint JSON example, automatic legacy expansion, the `infrastructure_layout` field, and the fact that the canonical topology repeats for all teams. Replace descriptions of raw JSON as the primary editor.

- [ ] **Step 2: Ignore visual-companion working files**

Add `.superpowers/` to `.gitignore`; do not delete or commit the existing mockup session files.

- [ ] **Step 3: Run the complete disposable test suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: all non-opt-in tests PASS with no failures.

- [ ] **Step 4: Validate deployment configuration and repository hygiene**

Run: `docker compose config >/dev/null`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only intentional documentation/source/test changes for this task; `.superpowers/` is absent because it is ignored.

- [ ] **Step 5: Commit final documentation**

```bash
git add README.md CLAUDE.md .gitignore
git commit -m "docs: document event network planner"
```

- [ ] **Step 6: Request final code review**

Invoke `superpowers:requesting-code-review` against the implementation range, resolve any correctness findings, and rerun the complete test suite plus `git diff --check` before declaring completion.
