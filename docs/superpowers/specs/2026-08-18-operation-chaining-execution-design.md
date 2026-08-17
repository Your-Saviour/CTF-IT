# Operation Chaining Execution

**Date:** 2026-08-18

## Purpose

Give red teams a way to chain vulnerabilities through each other into a single curated sweep — e.g. an RCE foothold that feeds a privilege escalation that installs an implant. Today the operation planner can *author* such a chain as a provider-neutral graph, but nothing executes it; the only live execution path runs pre-generated "Full Exploit Chain" adversaries with gating confined to each module's own recon→exploit pair.

This change makes the platform the orchestrator: it compiles a saved operation plan into a resolved run graph, executes it one ability at a time against live VMs via single-ability Caldera operations, and propagates outputs between stages through an explicit fact contract. Chains are success-gated with real data flow.

## Background

Two systems exist and do not meet in the middle:

- **Operation planner** (`builder/operation_plan.py`, `api/routes/admin.py`) — a provider-neutral DAG (`manual_trigger`/`event_start_trigger`/`scheduled_trigger` → `target`/`ability`/`objective`/`delay`/`gate` → `finish`) with `success`/`failure`/`always` edges and `gate` modes `all`/`any`/`first`. It validates, catalogues, and previews (`compile_team_preview`) but has **no executor**.
- **Caldera execution** (`builder/caldera.py`, `api/routes/caldera_ops.py`) — generates a plugin + adversaries. `POST /admin/api/caldera/operations` runs a named adversary over a group. Gating is native Caldera facts: recon emits `ctf.vuln.<id>`, its own exploit requires it, and goal exploits can gate on a prerequisite vuln's recon fact. There is no way for one vulnerability's **exploit output** to feed another vulnerability's **input**, and no platform-level ordering of a curated chain.

The fact substrate (`builder/caldera_plugin_app/parsers/ctf_extract.py`, `ctf_basic.py`) already supports marker + regex capture into fact values, and seeded VM facts (`ctf.hostname`, `ctf.ip`, `ctf.os`, `host.id`) are produced by `vm_source_facts()`.

## Design

### 1. Architecture and execution model

A new execution subsystem, in order of flow:

```
plan graph ──compile──▶ resolved run graph
   │                        │
   │        fact contract ──┤  (what each ability emits / consumes)
   ▼                        ▼
runner loop: pop next node → check inputs against fact store (skip if missing)
        → seed facts to per-run source → drive Caldera (single ability on target agent)
        → poll → parse output → store emitted facts → evaluate edge → enqueue next node(s)
```

**The platform owns ordering, data flow, and gating.** The runner executes **one ability per Caldera operation** (single-ability adversary, `autonomous=1`), polls to completion, reads output, and closes. This keeps the platform firmly in control of sequence, makes failure isolation trivial, and reuses the existing `create_operation` / `get_operation` / `seed_facts` plumbing. The platform's fact store is the single source of truth for skip decisions and fact extraction; before each step the runner seeds the current fact store into a **per-run Caldera source** (`ctf-run-{run_id}`), so Caldera substitutes `#{trait}` references natively while the platform retains ownership of the values and the ordering. Caldera's planner is not involved in ordering.

**Open implementation detail:** verify whether Caldera v5 supports `allowed_agents` for strict single-paw targeting; if not, gate via a per-run fact seeded only for the target agent. Confirm at implementation time.

### 2. Catalogue fact contract (data flow)

Generalize the narrow recon→exploit fact mechanism into an explicit, symmetric, module-scoped contract. All fields optional and backward-compatible.

```yaml
caldera:
  recon:
    command: ...
    outputs:                    # facts this ability emits on success
      - trait: ctf.weak_ssh_credentials.shell
        marker: VULNERABLE
        pattern: "user=(\\S+)"   # capture group → fact value
  exploit:
    command: ...                 # may reference #{ctf.weak_ssh_credentials.shell}
    inputs:                      # facts this ability consumes
      - ctf.weak_ssh_credentials.shell
    outputs:
      - trait: ctf.nopasswd_sudo.root
        marker: ROOT_SHELL
```

- **`outputs`** — after an ability runs, the runner scans stdout for `marker`; if present it applies `pattern` to capture a value and stores `{trait: value}` in the run's fact store. Multiple `outputs` per ability are allowed. A bare `marker` with no `pattern` stores a truthy marker (mirrors the existing `VULNERABLE` fact).
- **`inputs`** — declares which traits the command references; before execution the runner seeds the stored value into the run's per-run Caldera source and Caldera substitutes `#{trait}` natively. If an `input` trait is absent from the fact store, the ability is skipped (see Section 3).
- **Trait naming is module-scoped + explicit**: each module's own success fact stays auto-derived (`ctf.vuln.<module_id>` for recon, `ctf.goal.<goal_id>` for goal exploits) so existing modules keep working with no rewrite; cross-module outputs are named explicitly (`ctf.<module_id>.<name>`) to avoid collisions.
- **Backward compatibility** — the current recon→own-exploit gating is expressed as implicit `outputs` (recon) + `inputs` (exploit), auto-derived in `builder/caldera.py` from existing behaviour; no existing module changes are required.

**Validation** — extend the catalogue validation to check that `inputs` reference a trait another module declares as `outputs` (or a known seeded platform fact), that `pattern` compiles, and that explicitly named traits do not collide.

### 3. Run state machine and node semantics

**Run lifecycle:** `created → queued → running ⇄ awaiting_approval → completed | failed | cancelled`.

Traversal uses a worklist over the validated-acyclic plan (start at the trigger, enqueue downstream nodes as edges fire). A node activates when its incoming edges permit it; `gate` nodes implement join semantics.

| Node | Behaviour | Result |
|---|---|---|
| `manual_trigger` | waits for admin "Run" | success |
| `event_start_trigger` / `scheduled_trigger` | auto-fire at event start / after `offset_minutes` | success |
| `target` | no-op (declarative endpoint reference) | success |
| `ability` | resolve target VM → agent paw; skip if a declared `input` is missing; seed facts; execute single-ability Caldera op; poll; parse outputs | success (exit 0) / failure (non-zero after retries, or timeout) / skipped (missing input) |
| `objective` | check fact store for `ctf.goal.<goal_id>` | success if present, else failure |
| `delay` | sleep `seconds` | success |
| `gate` | `all`=await all predecessors; `any`/`first`=activate on first predecessor | passes through |
| `finish` | terminal; run `completed` (or `failed` if a required objective was unmet) | — |

**Locked-in semantics:**

1. **Skipped vs failed.** An ability whose declared `inputs` are absent from the fact store is marked `skipped`, not failed. It follows the **failure** edge (so a chain short-circuits when a prerequisite didn't materialize) but is reported distinctly ("not attempted — missing prerequisite").
2. **Retries.** `retries` re-runs on non-zero exit; `retry_delay_seconds` between attempts; `timeout_seconds` per attempt. Exhausted retries → failure.
3. **Human-in-loop.** `policy.instructor_approval=true` pauses before each `ability` node (`awaiting_approval`); approve → execute, reject → failure edge. Mirrors the existing approve/reject flow in `caldera_ops.py`.
4. **Concurrency.** `policy.max_concurrency` bounds parallel ability execution; default `1` = the strictly-sequential sweep. `>1` for independent branches is supported but lower priority than correct sequential gating.
5. **Skipped-input check is the gate, not Caldera.** "Did RCE produce a foothold?" is answered by whether the foothold fact exists when privesc runs — deterministic, no reliance on Caldera's planner.

### 4. Persistence, API, UI

**New models (one migration, `0016_operation_runs.py`):**

- **`OperationRun`** — `id`, `event_id` FK, `operation_id` FK (→ `EventOperation`), `team_id` FK (nullable; null = canonical), `status`, `plan_snapshot` (Text JSON, frozen at launch), `fact_store` (Text JSON — the run's fact map, module-scoped traits → values), `trigger` (Text JSON), `started_at`, `finished_at`, timestamps.
- **`OperationRunStep`** — `id`, `run_id` FK, `node_id`, `node_type`, `status` (`queued/running/awaiting_approval/success/failure/skipped`), `result`, `output` (Text, truncated), `attempts`, `caldera_operation_id` (nullable, for audit), `started_at`, `finished_at`.

`fact_store` is seeded at launch with the VM platform facts from `vm_source_facts()`.

**API (new endpoints in `api/routes/admin.py`, alongside the event-operation routes):**

| Endpoint | Purpose |
|---|---|
| `POST /admin/events/{event_id}/operations/{operation_id}/run` | compile + launch; body `{team_id?}` — omit to run for every team (one `OperationRun` each) |
| `GET /admin/events/{event_id}/operations/{operation_id}/runs` | list runs (status, team, progress) |
| `GET /admin/operation-runs/{run_id}` | detail: status, per-step results, fact store, graph node statuses |
| `POST /admin/operation-runs/{run_id}/steps/{step_id}/approve` / `reject` | human-in-loop |
| `POST /admin/operation-runs/{run_id}/cancel` | cancel a running/paused run |

Launch uses an asyncio background task (`asyncio.create_task(launch_run(run.id))`) so a sweep doesn't block the request.

**UI:**

- A **Run** action on the operation designer page (`/admin/events/{id}/operations/{operation_id}`).
- A **run detail view** reusing the existing graph rendering with per-node status coloring (like `annotate_tree_statuses`), plus a step log (output per step), the live fact store, and approve/reject controls when `instructor_approval` is on. Polls every few seconds (same pattern as the provisioning progress bar).

### 5. Showcase default event

At startup, next to the existing "Default CTF Event" (`api/main.py:177`), seed a **draft** event (idempotent, only if absent) that demonstrates chaining without requiring Vultr credentials:

- **Event**: "Operation Chaining Demo" (draft — never auto-provisioned).
- **Catalogue wiring**: add the fact-contract `outputs`/`inputs` (Section 2) to three chainable modules so the data flow is real:
  - `weak_ssh_credentials` exploit → `outputs: ctf.weak_ssh_credentials.shell` (foothold)
  - `nopasswd_sudo` exploit → `inputs: [ctf.weak_ssh_credentials.shell]`, `outputs: ctf.nopasswd_sudo.root` (privesc)
  - `malicious_cron_beacon` exploit → `inputs: [ctf.nopasswd_sudo.root]` (implant)
- **Infrastructure + module plan**: a minimal single blue endpoint (`vm:demo/site/box`, `ubuntu_24_server`) with the three modules pinned, so the plan validates and the graph renders a target.
- **Pre-authored `EventOperation`**: "RCE → Privilege Escalation → Implant" whose `operation_plan` graph is `trigger → exploit(weak_ssh) → exploit(nopasswd_sudo) → exploit(cron) → finish`, all `success` edges.

The demo is inert until an admin opens it and provisions a target VM.

### 6. Error handling, concurrency, scoring

- **Resilience**: unreachable VM / missing agent → step failure (follows failure edge); Caldera unavailable → run transitions to `failed` with a clear error and no orphaned single-ability operations (each is polled to completion before advancing). Per-step `timeout_seconds`; hard stop via `policy.time_limit_minutes`.
- **Concurrency / restart**: the runner is an asyncio background task; `max_concurrency` gates parallel ability nodes. On API restart, any `running` runs are marked `failed`/`interrupted`, mirroring the provisioning-interrupt handling at `api/main.py:165`.
- **Scoring**: `objective` nodes are gate/observability only. Scoring remains entirely verification-driven (`POST /admin/vms/{vm_id}/goals/{goal_id}/check`); the runner does not mutate `VMGoal.achievement_count`/`defend_count`.
- **Security**: commands are authored and sanitized at the module level; the runner only references pre-built abilities and seeds fact values, exposing no new raw-input surface.

### 7. Testing and rollout

- **Unit tests** (extend `tests/`, pure Python, no Caldera): fact-contract parsing/validation, plan compiler (endpoint resolution + ordering), and state-machine traversal (edge/gate/skip/retry semantics).
- **Integration tests** (disposable Docker test service): a single-ability Caldera op create/poll/parse round-trip, and an end-to-end three-stage sweep against a mock agent.
- **Rollout**: new migration; existing events/modules unaffected (all new fields optional); the fact contract defaults preserve current recon→exploit gating.

## Scope boundaries

- Only the three chain modules in the showcase get the explicit fact contract; the rest keep default gating.
- The operation-plan graph remains the single authoring surface; no second "chain" UI is added.
- Scoring, module selection, and attack-tree generation are unchanged. The attack tree's phase-ordering edges are not repurposed for runtime ordering — runtime order comes solely from the compiled plan graph.
- `max_concurrency > 1` is best-effort; correct sequential gating is the priority.

## Verification

Tests (written first, TDD):

- Fact-contract: loader parses `outputs`/`inputs`; validation rejects unknown input traits, uncompilable patterns, and colliding traits; auto-derived `ctf.vuln.<id>` / `ctf.goal.<id>` still produce the existing default gating.
- Compiler: resolves `target_vm_id` → concrete VM per team (by `site_id`/`zone_id`/`vm_type`/`team_id`); produces a deterministic traversal order; fails cleanly on an unprovisioned endpoint.
- State machine: `success`/`failure`/`always` edge routing, `gate` modes `all`/`any`/`first`, skip-on-missing-input (follows failure edge, reported as `skipped`), retries/timeout, approval pause/resume, cancel.
- Runner↔Caldera: single-ability operation create/poll/parse; output marker + pattern capture into the fact store; `#{trait}` substitution before execution.
- Showcase seed: idempotent creation of the draft demo event with the authored chain; absent on a second startup.

Authoritative regression coverage is the disposable Docker test service (`docker compose --profile test run --rm tests`), plus the existing attack-tree, caldera-builder, and operation-plan suites. Local `pytest` is used for fast red/green cycles.

## Acceptance criteria

- A saved operation plan can be launched and executes its abilities in order against live VMs, gated on stage success with data flow between stages.
- The three-module showcase chain (foothold → privesc → implant) is seeded as a draft event and runs end-to-end once a target is provisioned.
- Existing behaviour (module selection, scoring, objectives, attack-tree generation, current recon→exploit gating) does not regress.
- The operation designer gains a Run action and a run detail view with per-step output, fact store, and approval controls.
