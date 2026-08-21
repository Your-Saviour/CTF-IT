# Green-Team Infrastructure and Expo-IT Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator plan one shared green-team VM, assign the Expo-IT deployment module, provide an encrypted Git SSH-key fact, and start an event that provisions, isolates, validates, and automatically integrates Expo-IT.

**Architecture:** Extend the canonical GameNet topology with event-level green VMs and extend the module catalogue with an isolated deployment contract. Durable encrypted deployment facts and per-module state make installation retryable; successful Expo-IT output consumption creates owned integration records and erases the deploy key before the event opens.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy/Alembic, Pydantic, Jinja2, vanilla ES modules, Node test runner, pytest, Vultr GameNet provider, SSH, systemd/Docker Compose, Expo-IT management API.

**Spec:** `docs/superpowers/specs/2026-08-21-green-team-infrastructure-expo-it-design.md`

## Global Constraints

- Green VMs exist once per event and never repeat per team.
- Expo-IT uses `git@github.com:Your-Saviour/Expo-IT.git` and the fixed `stable` branch.
- Expo-IT is reachable by participants only through GameNet VPN gateways.
- Expo-IT cannot initiate connections into team workload networks.
- Secret facts never appear in topology/module-plan JSON, API reads, persisted commands, generated artifacts, progress messages, or logs.
- The encrypted Git SSH key remains until the complete deployment succeeds, then is deleted.
- Generated integration resources must be distinguishable from administrator-managed resources and cleanup must preserve the latter.
- Existing events without `green_infrastructure` retain current behavior.
- Run Python tests through `docker compose --profile test run --rm tests`, as required by `CLAUDE.md`.

## File Structure

- `migrations/versions/0018_green_infrastructure.py` — schema for green VM identity, encrypted deployment facts/state, and generated integration ownership.
- `api/models.py` — SQLAlchemy models and relationships for deployment facts/state and owned integration resources.
- `builder/infrastructure_planner.py` — normalization and stable node IDs for event-level green VMs.
- `builder/infrastructure_validation.py` — green topology validation, sizing, and hostname helpers.
- `builder/module_loader.py` — deployment module and input/output fact schema.
- `builder/module_plan.py` — assignable green-node support and deployment-only compatibility.
- `modules/green_infrastructure/expo_it/expo_it.yaml` — fixed Expo-IT deployment definition and fact contract.
- `modules/green_infrastructure/expo_it/install.sh` — idempotent host installation entrypoint with runtime inputs.
- `api/services/deployment_facts.py` — secret storage, redacted presence, input resolution, output consumption, and cleanup.
- `api/services/green_deployment.py` — generic dependency-ordered deployment executor and Expo-IT completion/health adapter.
- `api/services/gamenet.py` — green service address allocation and participant VPN routes.
- `api/services/gamenet_provider.py` — green VM firewall, route, host-firewall, and acceptance primitives.
- `api/services/gamenet_provisioning.py` — green phases in the durable GameNet state machine.
- `api/services/green_integrations.py` — idempotent conversion of Expo-IT outputs into owned integration records.
- `api/routes/admin.py` — green fact endpoints, catalogue serialization, preflight, status, and retry integration.
- `api/routes/integrations.py` — owned-resource mutation guards.
- `frontend/static/event-planner-state.js` — green topology normalization/index/update helpers.
- `frontend/static/event-planner-canvas.js` — event-level green grouping and node rendering.
- `frontend/static/event-planner.js` — green node creation/editing.
- `frontend/static/event-modules-state.js` — deployment-module filtering and required-fact state helpers.
- `frontend/static/event-modules.js` — write-only fact controls and green assignment behavior.
- `frontend/static/event-planner.css`, `frontend/static/event-modules.css` — green group and secret-state presentation.
- `frontend/templates/event_plan.html`, `frontend/templates/event_modules.html` — green actions and accurate shared/repeated copy.
- `tests/` JavaScript and Python files listed by task — focused TDD coverage.
- `README.md`, `TEST_PLAN.md` — administrator workflow and opt-in live acceptance instructions.

---

### Task 1: Persist Green Deployment Identity, Facts, State, and Ownership

**Files:**
- Create: `migrations/versions/0018_green_infrastructure.py`
- Modify: `api/models.py`
- Test: `tests/test_green_deployment_models.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `GreenDeploymentFact(event_id, vm_key, trait, encrypted_value, secret, created_at, updated_at)`.
- Produces: `GreenDeploymentState(vm_id, module_id, status, current_step, resolved_commit, service_url, health_status, error_code, error_message, created_at, updated_at, completed_at)`.
- Produces: nullable `VM.team_id`, `VM.green_key`, and unique `(event_id, green_key)` for non-null green keys.
- Produces: nullable `IntegrationDestination.owner_green_vm_id` and `ServiceCredential.owner_green_vm_id`.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_green_deployment_models_encrypt_identity_and_ownership(db_session):
    event = Event(name="Green", quota="{}", status="draft")
    db_session.add(event); db_session.flush()
    vm = VM(event_id=event.id, team_id=None, green_key="expo_it", role="green_service")
    db_session.add(vm); db_session.flush()
    fact = GreenDeploymentFact(
        event_id=event.id, vm_key="expo_it", trait="git.ssh_private_key",
        encrypted_value="enc:v1:ciphertext", secret=True,
    )
    state = GreenDeploymentState(vm_id=vm.id, module_id="expo_it", status="pending")
    db_session.add_all([fact, state]); db_session.commit()
    assert vm.team_id is None
    assert state.vm.green_key == "expo_it"

def test_generated_integration_records_reference_green_owner(db_session):
    # Create a green VM, credential, and destination using repository fixtures.
    credential.owner_green_vm_id = vm.id
    destination.owner_green_vm_id = vm.id
    db_session.commit()
    assert destination.credential.owner_green_vm_id == vm.id
```

- [ ] **Step 2: Run tests to verify the schema is missing**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment_models.py tests/test_migrations.py -q`

Expected: FAIL because the new models, columns, and revision do not exist.

- [ ] **Step 3: Add migration `0018_green_infrastructure`**

Create tables with unique constraints on `(event_id, vm_key, trait)` and `(vm_id, module_id)`, indexes for event/VM lookup, nullable foreign keys with `CASCADE` for fact/state rows, and `SET NULL` for integration ownership. Alter `vms.team_id` to nullable, add `vms.green_key`, add the partial unique event/green-key index, and add ownership columns to service credentials and destinations. Provide a downgrade that reverses only data-preserving schema additions; document that downgrade cannot make `team_id` non-null while green rows exist.

- [ ] **Step 4: Add SQLAlchemy models and relationships**

```python
class GreenDeploymentFact(Base):
    __tablename__ = "green_deployment_facts"
    __table_args__ = (UniqueConstraint("event_id", "vm_key", "trait",
                                      name="uq_green_fact_scope"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    vm_key: Mapped[str] = mapped_column(String(64))
    trait: Mapped[str] = mapped_column(String(128))
    encrypted_value: Mapped[str] = mapped_column(Text)
    secret: Mapped[bool] = mapped_column(Boolean, default=True)

class GreenDeploymentState(Base):
    __tablename__ = "green_deployment_states"
    __table_args__ = (UniqueConstraint("vm_id", "module_id",
                                      name="uq_green_deployment_module"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"))
    module_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="pending")
```

Complete the fields and relationships named in Interfaces and use the repository's `utcnow` defaults.

- [ ] **Step 5: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment_models.py tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0018_green_infrastructure.py api/models.py tests/test_green_deployment_models.py tests/test_migrations.py
git commit -m "feat: persist green deployment state"
```

### Task 2: Extend Infrastructure and Module Contracts

**Files:**
- Modify: `builder/infrastructure_planner.py`
- Modify: `builder/infrastructure_validation.py`
- Modify: `builder/module_loader.py`
- Modify: `builder/module_plan.py`
- Create: `modules/green_infrastructure/expo_it/expo_it.yaml`
- Create: `modules/green_infrastructure/expo_it/install.sh`
- Test: `tests/test_event_plan_template.py`
- Test: `tests/test_module_plan.py`
- Test: `tests/test_green_module_contract.py`

**Interfaces:**
- Produces: normalized `green_infrastructure: {"vms": []}`.
- Produces: stable IDs `green:<key>` from `assignable_endpoints()` with `role="green"` and `shared=True`.
- Produces: `DeploymentFactSpec(trait, label, value_type, secret, consume_as)` and `DeploymentContract(inputs, outputs, completion_check)` on `Module.deployment`.
- Produces: `gamenet_green_hostname(event_id, key) -> str`.

- [ ] **Step 1: Write failing normalization, validation, and assignment tests**

```python
def test_normalize_adds_empty_green_infrastructure_to_legacy_plan():
    value = normalize_infrastructure({"vpn_gateway": GATEWAY, "sites": SITES})
    assert value["green_infrastructure"] == {"vms": []}

def test_green_vm_is_assignable_once_and_only_accepts_deployment_modules():
    infra = {**INFRASTRUCTURE, "green_infrastructure": {"vms": [{
        "key": "expo_it", "name": "Expo-IT", "base_type": "ubuntu_24_server",
        "default_plan": "vc2-1c-2gb", "region": "syd",
    }]}}
    green = next(row for row in assignable_endpoints(infra) if row["id"] == "green:expo_it")
    assert green["shared"] is True
    result = resolve_assignment(green, {"mode": "manual_only", "pinned_module_ids": ["expo_it"]},
                                {}, [EXPO_MODULE], refill=False)
    assert result["resolved_module_ids"] == ["expo_it"]
```

Add cases for duplicate keys, malformed machines, unavailable regions/bases, multiple Expo-IT assignments, endpoint modules on green nodes, and green deployment modules on team endpoints.

- [ ] **Step 2: Run the contract tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_module_plan.py tests/test_green_module_contract.py -q`

Expected: FAIL on missing green normalization and deployment schema.

- [ ] **Step 3: Implement green topology normalization, IDs, validation, summary, and hostname**

Update allowed top-level keys, validate every green machine with required region, add its count once to `infrastructure_summary`, include `green:<key>` in layout node IDs, and preserve the empty default for legacy documents.

- [ ] **Step 4: Implement deployment-module parsing and validation**

```python
@dataclass(frozen=True)
class DeploymentFactSpec:
    trait: str
    label: str
    value_type: str = "string"
    secret: bool = False
    consume_as: str | None = None

@dataclass(frozen=True)
class DeploymentContract:
    inputs: tuple[DeploymentFactSpec, ...]
    outputs: tuple[DeploymentFactSpec, ...]
    completion_check: dict
```

Parse an optional `deployment` object, require it for `type: green_infrastructure`, reject duplicate/invalid traits, require all secret inputs to use write-only handling, and expose a boolean deployment capability for assignment filtering.

- [ ] **Step 5: Add the built-in Expo-IT module**

```yaml
id: expo_it
name: Expo-IT
description: Shared green-team exercise management and reporting service.
type: green_infrastructure
difficulty: medium
points: 0
category: exercise-management
tags: [green-team, reporting, integration]
supported_bases: [ubuntu_24_server]
min_ram_mb: 2048
min_vcpu: 1
steps:
  - run: install.sh
deployment:
  repository: git@github.com:Your-Saviour/Expo-IT.git
  branch: stable
  inputs:
    - trait: git.ssh_private_key
      label: Git SSH private key
      value_type: ssh_private_key
      secret: true
  outputs:
    - {trait: expo_it.resolved_commit, label: Resolved commit, value_type: string, secret: false}
    - {trait: expo_it.private_url, label: Private URL, value_type: url, secret: false}
    - {trait: expo_it.api_key, label: API key, value_type: token, secret: true, consume_as: expo_it_integration}
  completion_check: {type: expo_it_management_api}
```

Make `install.sh` accept paths/values supplied by the executor, use `GIT_SSH_COMMAND` with strict host checking, fetch `origin stable`, checkout the resolved remote commit, invoke Expo-IT's documented build/start commands, and print only machine-readable non-secret outputs. API-key transfer must use a protected output file, not stdout.

- [ ] **Step 6: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_module_plan.py tests/test_green_module_contract.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add builder modules/green_infrastructure tests/test_event_plan_template.py tests/test_module_plan.py tests/test_green_module_contract.py
git commit -m "feat: define green deployment modules"
```

### Task 3: Add Encrypted Write-Only Deployment Fact APIs

**Files:**
- Create: `api/services/deployment_facts.py`
- Modify: `api/routes/admin.py`
- Test: `tests/test_green_deployment_facts.py`
- Test: `tests/test_admin_authorization.py`

**Interfaces:**
- Produces: `declared_inputs(event, vm_key) -> dict[str, DeploymentFactSpec]`.
- Produces: `fact_presence(db, event_id, vm_key) -> list[dict]` with no value field.
- Produces: `resolve_inputs(db, event_id, vm_key, module) -> dict[str, str]` for executor-only use.
- Produces: `PUT /admin/api/events/{event_id}/green/{vm_key}/facts/{trait}` with `{"value": "..."}`.
- Produces: `DELETE` and redacted `GET` at the same green-node fact scope.

- [ ] **Step 1: Write failing service and route tests**

```python
def test_secret_fact_round_trip_is_encrypted_and_read_is_presence_only(client, db_session, admin_headers):
    response = client.put(
        f"/admin/api/events/{event.id}/green/expo_it/facts/git.ssh_private_key",
        headers=admin_headers, json={"value": PRIVATE_KEY},
    )
    assert response.status_code == 200
    row = db_session.query(GreenDeploymentFact).one()
    assert PRIVATE_KEY not in row.encrypted_value
    body = client.get(f"/admin/api/events/{event.id}/green/expo_it/facts",
                      headers=admin_headers).json()
    assert body == [{"trait": "git.ssh_private_key", "configured": True,
                     "secret": True, "label": "Git SSH private key",
                     "updated_at": row.updated_at.isoformat()}]
    assert PRIVATE_KEY not in response.text + json.dumps(body)
```

Add tests for non-admin denial, non-draft denial, undeclared traits, malformed SSH keys, replace, clear, node/module removal, and executor-only decryption.

- [ ] **Step 2: Run tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment_facts.py tests/test_admin_authorization.py -q`

Expected: FAIL because the service and routes are absent.

- [ ] **Step 3: Implement fact service with encryption and contract lookup**

Use `encrypt_secret()`/`decrypt_secret()`, validate OpenSSH/PEM private-key framing without invoking a shell, and return only declared metadata plus presence. Keep `resolve_inputs` internal to provisioning code; do not add a route that reveals values.

- [ ] **Step 4: Implement admin routes and orphan clearing on module-plan save**

Require an administrator, a draft event, an existing green node, and an assigned module declaring the trait. When saving a changed module plan, compare valid `(vm_key, trait)` pairs and delete facts that are no longer declared only after the request explicitly includes `confirm_removed_secret_facts: true`; otherwise return `409` with the affected traits.

- [ ] **Step 5: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment_facts.py tests/test_admin_authorization.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/deployment_facts.py api/routes/admin.py tests/test_green_deployment_facts.py tests/test_admin_authorization.py
git commit -m "feat: store write-only deployment facts"
```

### Task 4: Add Green Nodes and Secret Controls to the Planner Workflow

**Files:**
- Modify: `frontend/static/event-planner-state.js`
- Modify: `frontend/static/event-planner-canvas.js`
- Modify: `frontend/static/event-planner.js`
- Modify: `frontend/static/event-planner.css`
- Modify: `frontend/templates/event_plan.html`
- Modify: `frontend/static/event-modules-state.js`
- Modify: `frontend/static/event-modules.js`
- Modify: `frontend/static/event-modules.css`
- Modify: `frontend/templates/event_modules.html`
- Test: `tests/event-planner-state.test.mjs`
- Test: `tests/event-planner-canvas.test.mjs`
- Test: `tests/event-modules-state.test.mjs`
- Test: `tests/test_event_modules_template.py`
- Test: `tests/test_event_plan_template.py`

**Interfaces:**
- Consumes: `green:<key>` VM rows and module `deployment.inputs` metadata from Task 2.
- Consumes: redacted fact endpoints from Task 3.
- Produces: planner actions `addGreenVm`, `updateGreenVm`, `removeGreenVm`.
- Produces: write-only fact state `missing | configured | saving | failed`.

- [ ] **Step 1: Write failing JavaScript state tests**

```javascript
test('green VMs normalize once and enter the node index', () => {
  const state = normalizeClientInfrastructure({...infrastructure,
    green_infrastructure:{vms:[{key:'expo_it',name:'Expo-IT',base_type:'ubuntu',default_plan:'small',region:'syd'}]}});
  assert.equal(nodeIndex(state).get('green:expo_it').type, 'green_vm');
});

test('deployment modules are available only to green nodes', () => {
  const modules=[{id:'expo_it',type:'green_infrastructure',deployment:{inputs:[]},supported_bases:['ubuntu']}];
  assert.deepEqual(filterModulesForVm(modules,{role:'green',base_type:'ubuntu'}).map(x=>x.id), ['expo_it']);
  assert.deepEqual(filterModulesForVm(modules,{role:'blue',base_type:'ubuntu'}), []);
});
```

Add tests for green add/edit/delete, layout pruning, secret-state rendering data, and confirmation when removal would clear configured facts.

- [ ] **Step 2: Run JavaScript/template tests to verify failure**

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-modules-state.test.mjs`

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_event_modules_template.py -q`

Expected: FAIL on missing green controls and helpers.

- [ ] **Step 3: Implement planner state and canvas grouping**

Normalize an empty green collection, index green nodes, preserve/prune their layout, render a clearly labelled event-level green group outside team-repeated sites, and expose add/edit/remove controls with the same base/plan/region catalogue validation used by other machines.

- [ ] **Step 4: Implement module assignment filtering and fact controls**

Serialize `deployment` metadata from the module-plan API. In the module workspace, label green VMs as `Shared / Green team`, force `manual_only`, show only deployment modules, and render required input controls in the selected module details. Submit secret values directly to the fact endpoint, discard input values from browser state after each request, and reload presence state.

- [ ] **Step 5: Update planner copy and styles**

Change “Canonical network repeated for every team” to explain that team sites repeat while green infrastructure is shared. Add “+ Add green VM”, accessible green group/node labels, configured/missing secret badges, and Replace/Clear controls. Preserve keyboard and read-only behavior.

- [ ] **Step 6: Run focused frontend tests and syntax checks**

Run: `node --check frontend/static/event-planner-state.js && node --check frontend/static/event-planner-canvas.js && node --check frontend/static/event-planner.js && node --check frontend/static/event-modules-state.js && node --check frontend/static/event-modules.js`

Run: `node --test tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-modules-state.test.mjs`

Run: `docker compose --profile test run --rm tests pytest tests/test_event_plan_template.py tests/test_event_modules_template.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend tests/event-planner-state.test.mjs tests/event-planner-canvas.test.mjs tests/event-modules-state.test.mjs tests/test_event_plan_template.py tests/test_event_modules_template.py
git commit -m "feat: plan shared green infrastructure"
```

### Task 5: Build a Retry-Safe Deployment Executor

**Files:**
- Create: `api/services/green_deployment.py`
- Modify: `api/services/ssh_connection.py`
- Modify: `api/services/gamenet_provisioning.py`
- Test: `tests/test_green_deployment.py`
- Test: `tests/test_gamenet.py`

**Interfaces:**
- Consumes: `resolve_inputs()` and deployment contracts.
- Produces: `execute_green_modules(db, event, vm, module_ids) -> dict[str, str]`.
- Produces: `expo_it_completion_check(vm, expected_commit, private_url, api_key) -> bool`.
- Produces: state-machine phases `create_green_services`, `deploy_green_modules`, `validate_green_services`.

- [ ] **Step 1: Write failing executor tests with fake SSH transport**

```python
def test_executor_resumes_completed_steps_without_exposing_secret(db_session, fake_ssh):
    fake_ssh.completion_results = [False, True]
    outputs = execute_green_modules(db_session, event, green_vm, ["expo_it"])
    assert outputs["expo_it.resolved_commit"] == "abc123"
    assert PRIVATE_KEY not in " ".join(fake_ssh.commands)
    assert PRIVATE_KEY not in db_session.query(GreenDeploymentState).one().error_message

def test_failed_build_retains_encrypted_input_and_retry_reuses_vm(db_session, fake_provider):
    fake_provider.fail_build = True
    provision_event_gamenets(event.id)
    assert db_session.get(Event, event.id).status == "provision_failed"
    assert db_session.query(GreenDeploymentFact).count() == 1
    assert db_session.query(VM).filter_by(green_key="expo_it").count() == 1
```

Cover dependency order, protected temporary files, output parsing, API-key output file handling, completion skip, repair, timeout, Git/build/health categories, redacted diagnostics, and state persistence.

- [ ] **Step 2: Run executor tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment.py tests/test_gamenet.py -q`

Expected: FAIL because the executor and phases do not exist.

- [ ] **Step 3: Implement generic executor and redaction boundary**

Stage module assets using existing source directories. Write secret inputs to randomly named mode-`0600` remote files, pass only file paths to scripts, capture non-secret JSON output separately, retrieve secret outputs from mode-`0600` files, and delete all temporary local/remote materials in `finally`. Convert raw exceptions into stable codes such as `git_failed`, `build_failed`, `health_failed`, and `secret_cleanup_failed` with sanitized messages.

- [ ] **Step 4: Implement Expo-IT completion and health checks**

Verify the checkout commit, service state, private health endpoint, and authenticated `GET /api/v1/data` contract. Reuse `ExpoData` validation without logging the API token or response bodies containing secrets.

- [ ] **Step 5: Integrate green placeholders and executor phases**

Materialize `VM(team_id=None, green_key=key, role="green_service")` before provider calls, create each provider instance once, store green module state per VM, and resume the first incomplete phase on Retry. Do not delete the input fact here; final orchestration owns deletion after networking and integration success.

- [ ] **Step 6: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_deployment.py tests/test_gamenet.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/services/green_deployment.py api/services/ssh_connection.py api/services/gamenet_provisioning.py tests/test_green_deployment.py tests/test_gamenet.py
git commit -m "feat: execute retry-safe green deployments"
```

### Task 6: Enforce VPN-Only One-Way Green-Service Networking

**Files:**
- Modify: `api/services/gamenet.py`
- Modify: `api/services/gamenet_provider.py`
- Modify: `api/services/gamenet_provisioning.py`
- Test: `tests/test_gamenet.py`
- Test: `tests/test_training_release.py`

**Interfaces:**
- Produces: `green_service_routes(db, event_id) -> list[str]`.
- Produces: `configure_green_service_access(event, vm, gateways) -> None`.
- Produces: acceptance results `green_vpn_access`, `green_public_denial`, `green_egress_isolation`.

- [ ] **Step 1: Write failing route, firewall, and ordering tests**

```python
def test_user_vpn_config_routes_shared_green_service(db_session):
    config = render_user_config(db_session, learner)
    assert f"{green_vm.ip_address}/32" in config

def test_green_firewall_allows_gateway_https_and_denies_team_egress(fake_provider):
    configure_green_service_access(event, green_vm, gateways)
    assert fake_provider.inbound == [{"protocol":"tcp", "port":"443", "sources":gateway_ips}]
    assert all(team_cidr in fake_provider.denied_egress for team_cidr in team_cidrs)
```

Also assert temporary SSH exists before install, final policy is applied before acceptance, public probes fail, each VPN path succeeds, and original team-isolation assertions remain.

- [ ] **Step 2: Run networking tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_training_release.py -q`

Expected: FAIL on absent green routes and policies.

- [ ] **Step 3: Add green routes to WireGuard configuration**

Return only active event-owned green service `/32` routes and append them to participant `AllowedIPs`. Update gateway configuration so forwarded HTTPS is allowed only to green services while peer/team route boundaries remain intact.

- [ ] **Step 4: Add provider and host firewall primitives**

Use resolved VM/gateway addresses rather than environment expansion. Create a provider firewall with TCP 443 sources limited to the event gateway public addresses and temporary SSH limited to `CTF_CONTROL_PLANE_CIDR`. Apply a host policy that permits established replies and required package/DNS access but rejects new connections to every allocated team site CIDR.

- [ ] **Step 5: Extend acceptance checks and lockdown ordering**

Require successful HTTPS probes through every team gateway, a failed unapproved-public probe, and failed green-to-team initiation before setting the event open. Remove temporary SSH only after the installer no longer needs it and before acceptance.

- [ ] **Step 6: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_gamenet.py tests/test_training_release.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/services/gamenet.py api/services/gamenet_provider.py api/services/gamenet_provisioning.py tests/test_gamenet.py tests/test_training_release.py
git commit -m "feat: isolate green services behind GameNet VPN"
```

### Task 7: Consume Expo-IT Outputs into an Owned Integration

**Files:**
- Create: `api/services/green_integrations.py`
- Modify: `api/services/deployment_facts.py`
- Modify: `api/services/gamenet_provisioning.py`
- Modify: `api/routes/integrations.py`
- Test: `tests/test_green_integrations.py`
- Test: `tests/test_integrations_api.py`
- Test: `tests/test_integration_outbox.py`

**Interfaces:**
- Produces: `ensure_expo_it_integration(db, event, vm, outputs) -> EventIntegration`.
- Produces: `finalize_green_deployment(db, event, vm, module_id) -> None`.
- Consumes: `expo_it.private_url`, `expo_it.api_key`, and integration outbox enqueueing.

- [ ] **Step 1: Write failing idempotency, transaction, and cleanup tests**

```python
def test_expo_outputs_create_owned_enabled_binding_and_initial_job(db_session):
    binding = ensure_expo_it_integration(db_session, event, green_vm, OUTPUTS)
    assert binding.enabled is True
    assert binding.destination.owner_green_vm_id == green_vm.id
    assert binding.destination.credential.owner_green_vm_id == green_vm.id
    assert decrypt_secret(binding.destination.credential.password) == OUTPUTS["expo_it.api_key"]
    assert binding.jobs[0].priority > 0

def test_finalize_deletes_input_only_after_integration_and_acceptance(db_session):
    finalize_green_deployment(db_session, event, green_vm, "expo_it")
    assert db_session.query(GreenDeploymentFact).count() == 0
```

Add cases for retry updating the same records, transaction rollback, conflicting administrator binding, generated cleanup, and preservation of administrator-managed records.

- [ ] **Step 2: Run integration tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_integrations.py tests/test_integrations_api.py tests/test_integration_outbox.py -q`

Expected: FAIL because output consumption and ownership guards are absent.

- [ ] **Step 3: Implement transactional owned integration creation**

Look up generated records by `owner_green_vm_id`; create or update one encrypted token credential and destination, then create/enable the event binding using existing adapter uniqueness rules. Reject an enabled administrator-owned Expo-IT binding with a stable preflight conflict rather than replacing it. Enqueue a priority `green_deployment_completed` sync only after the records commit.

- [ ] **Step 4: Add mutation and cleanup guards**

Prevent ordinary destination deletion or credential replacement from silently breaking an active generated deployment. The explicit infrastructure-destroy service may remove the binding, then owned destination and credential in dependency order. Never follow null ownership to delete an administrator-managed resource.

- [ ] **Step 5: Finalize secrets only at the terminal success boundary**

After installation, health, networking acceptance, integration setup, and sync enqueueing have all succeeded, delete `git.ssh_private_key`, mark deployment state complete, and commit together. A cleanup failure leaves the event closed and retryable.

- [ ] **Step 6: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_green_integrations.py tests/test_integrations_api.py tests/test_integration_outbox.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/services/green_integrations.py api/services/deployment_facts.py api/services/gamenet_provisioning.py api/routes/integrations.py tests/test_green_integrations.py tests/test_integrations_api.py tests/test_integration_outbox.py
git commit -m "feat: automate Expo-IT integration setup"
```

### Task 8: Complete Preflight, Status, Retry, and Redaction

**Files:**
- Modify: `api/routes/admin.py`
- Modify: `api/routes/event_dashboard.py`
- Modify: `frontend/templates/event_dashboard.html`
- Test: `tests/test_event_lifecycle.py`
- Test: `tests/test_event_dashboard.py`
- Test: `tests/test_green_redaction.py`

**Interfaces:**
- Produces: preflight issue shape `{"code", "message", "vm_id", "vm_key", "module_id", "trait"}`.
- Produces: green provision-status fields `ownership`, `green_key`, `module_id`, `resolved_commit`, `service_url`, `health_status`, `integration_status`.

- [ ] **Step 1: Write failing lifecycle and redaction tests**

```python
def test_start_blocks_before_provider_mutation_when_green_fact_is_missing(client, fake_provider):
    response = client.post(f"/admin/api/events/{event.id}/start", headers=ADMIN)
    assert response.status_code == 422
    assert response.json()["details"][0]["trait"] == "git.ssh_private_key"
    assert fake_provider.calls == []

def test_status_and_failure_payloads_never_contain_green_secrets(client):
    body = client.get(f"/admin/api/events/{event.id}/provision-status", headers=ADMIN).text
    assert PRIVATE_KEY not in body
    assert API_KEY not in body
```

Cover capacity/provider validation, existing binding conflict, retry from each green phase, status provenance, and opening only after terminal finalization.

- [ ] **Step 2: Run lifecycle tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_lifecycle.py tests/test_event_dashboard.py tests/test_green_redaction.py -q`

Expected: FAIL on missing preflight/status behavior.

- [ ] **Step 3: Implement complete read-only preflight before mutations**

Validate topology, resolved green assignments, required facts, one Expo-IT maximum, base/provider capacity, credentials, and existing integration conflicts before calling address allocation or placeholder creation. Return structured issues with planner/module links.

- [ ] **Step 4: Extend status and dashboard presentation**

Include green VMs in counts without assigning a team name. Display `Shared green infrastructure`, current safe step, commit, VPN URL, health, and integration sync status. Keep secret fields absent from serializers and escape all displayed strings.

- [ ] **Step 5: Centralize redacted failure handling**

Add a sanitizer that replaces every currently resolved secret and known key/token patterns before truncating stored errors. Call it at SSH, executor, state-machine, and route boundaries; ensure raw subprocess output is retained only in memory long enough to classify the failure.

- [ ] **Step 6: Run focused tests**

Run: `docker compose --profile test run --rm tests pytest tests/test_event_lifecycle.py tests/test_event_dashboard.py tests/test_green_redaction.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/routes/admin.py api/routes/event_dashboard.py frontend/templates/event_dashboard.html tests/test_event_lifecycle.py tests/test_event_dashboard.py tests/test_green_redaction.py
git commit -m "feat: expose green deployment lifecycle"
```

### Task 9: Document and Verify the End-to-End Release

**Files:**
- Modify: `README.md`
- Modify: `TEST_PLAN.md`
- Create: `tests/test_expo_it_green_live.py`
- Modify: `pytest.ini`
- Test: entire existing suite

**Interfaces:**
- Produces: pytest marker `expo_it_green_live`.
- Produces: documented administrator path from planner assignment through synchronized Expo-IT.

- [ ] **Step 1: Write the opt-in live acceptance test**

```python
pytestmark = [pytest.mark.expo_it_green_live, pytest.mark.asyncio]

async def test_stable_branch_build_produces_authenticated_managed_expo(tmp_path, green_host):
    result = await provision_expo_it_live(
        repository="git@github.com:Your-Saviour/Expo-IT.git",
        branch="stable",
        ssh_key_path=os.environ["EXPO_IT_GIT_SSH_KEY_PATH"],
        target=green_host,
    )
    assert result.resolved_commit
    assert result.private_url.startswith("https://")
    data = await ExpoITTransport(result.private_url, result.api_key).get_data()
    ExpoData.model_validate(data)
```

Skip unless the target-host and key-path environment variables are present. Assert test cleanup removes the test VM/container and temporary key material.

- [ ] **Step 2: Run the default suite before documentation changes**

Run: `docker compose --profile test run --rm --build tests`

Expected: PASS with `expo_it_green_live` skipped.

- [ ] **Step 3: Document configuration, security, retry, and cleanup**

In `README.md`, document adding a green VM, assigning Expo-IT, entering the write-only key, required Vultr/control-plane settings, VPN-only URL behavior, automatic binding, Retry, key deletion, and explicit destroy ownership. In `TEST_PLAN.md`, document the live test environment variables and manual checks for public denial, every team VPN path, and green-to-team egress denial.

- [ ] **Step 4: Run live acceptance when credentials are available**

Run: `docker compose --profile test run --rm -e EXPO_IT_GREEN_LIVE=1 -e EXPO_IT_GIT_SSH_KEY_PATH -e EXPO_IT_GREEN_TARGET tests pytest tests/test_expo_it_green_live.py -m expo_it_green_live -q`

Expected: PASS when the external prerequisites are supplied; otherwise record the explicit skip and complete the manual infrastructure checklist before release.

- [ ] **Step 5: Run final verification**

Run: `docker compose --profile test run --rm --build tests`

Run: `docker compose config >/dev/null`

Run: `git diff --check`

Expected: full default suite PASS, Compose configuration valid, and no whitespace errors.

- [ ] **Step 6: Commit**

```bash
git add README.md TEST_PLAN.md pytest.ini tests/test_expo_it_green_live.py
git commit -m "docs: add Expo-IT green deployment operations"
```
