# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CTF training platform for VM-based red team / blue team exercises. Admins create events, define teams, and provision Vultr VMs with randomized vulnerability and hardening modules via Ansible. The platform handles module selection, VM provisioning, Caldera red team emulation, goal tracking, and scoring.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn api.main:app --reload

# Run with Docker Compose (production)
docker compose up -d

# Run with AI agent enabled
docker compose --profile ai-agent up -d
```

### Testing

See [TEST_PLAN.md](TEST_PLAN.md) for the integration test plan.

- Build tests: `docker compose --profile test build tests`
- Run unit/integration tests: `docker compose --profile test run --rm tests`
- Always test via the disposable Docker test service, not by importing Python modules directly

### Required Environment Variables

See `.env.example` for full documentation. Key variables:

- `SECRET_KEY` — used for session signing
- `DATA_ENCRYPTION_KEY` — encrypts infrastructure credentials stored in the database
- `ADMIN_BOOTSTRAP_TOKEN` — required by the first account registration on a fresh database
- `DATABASE_URL` — defaults to `sqlite:///ctf.db`, use postgres URI for production
- `EVENT_QUOTA` — JSON defining module selection counts per type/difficulty, with optional `categories` and `tags` keys for additional filtering (see `.env.example`)
- `API_PORT` — port the API is exposed on (default `8080`)
- `SEMAPHORE_URL` / `SEMAPHORE_ADMIN` / `SEMAPHORE_ADMIN_PASSWORD` — Ansible Semaphore connection (internal service; defaults work with docker-compose stack)
- `VULTR_API_KEY` / `VULTR_DEFAULT_REGION` — Vultr cloud provisioning (optional; enables VM create/destroy from admin UI)
- `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_DOMAIN` — Cloudflare DNS (optional; auto-creates DNS A records for provisioned VMs)
- `AGENT_API_KEY` — shared key for CTF API ↔ AI agent communication (required for agent service)
- `AI_API_BASE` / `AI_API_KEY` / `AI_MODEL` — OpenAI-compatible API for AI agent (required for agent service)

## Architecture

### Request Flow

Admin creates an event with a module quota and VM quota → admin creates teams → starting the event auto-provisions Vultr VMs per team via Semaphore/Ansible → modules are selected and applied to target VMs → Caldera red team plugin is generated and loaded → Caldera runs adversary operations against target VMs → goal achievements and defences are tracked via `VMGoal` records → red vs blue scoreboard at `GET /admin/caldera/scoreboard`.

### Key Components

- **`api/`** — FastAPI app serving HTML templates (Jinja2) and JSON API endpoints. Routes split into `auth`, `admin`, `ai_agent` (proxy to agent service), `ansible_export`, `caldera_export`, `caldera_setup`, `caldera_ops`, `caldera_tree`, `vm` (Team/VM CRUD, topology data), `vm_goals` (VMGoal state machine API).
- **`ai_agent/`** — AI red team agent service (separate FastAPI app). EGATS planner with UCB node selection, TDI difficulty scoring, promise backpropagation. Communicates with main API via HTTP. See "AI Red Team Agent" section for details.
- **`builder/`** — Module orchestration. `ansible.py` generates Ansible playbooks from selected modules for VM provisioning. `caldera.py` generates MITRE Caldera plugin exports (abilities + adversaries) from selected modules; supports both flat (single adversary) and multi-path (per-path adversaries with skip logic) modes. `attack_tree.py` builds directed acyclic graphs (attack trees) from a VM's assigned modules, ordered by ATT&CK kill chain phase, and extracts distinct attack paths via DFS. `vm_quota_validation.py` validates the vm_quota JSON schema. `plan_sizing.py` picks the cheapest Vultr plan that meets module resource requirements.
- **`modules/`** — Self-contained YAML definitions + optional shell scripts for vulnerabilities (`vulns/`), hardening tasks (`hardening/`), payloads (`payloads/`), external applications (`application_external/`), internal applications (`application_internal/`), and red team objectives (`goals/`). Adding a new module = adding a YAML + optional .sh file, no code changes needed.
- **`templates/playbook.yml.j2`** — Jinja2 template for Ansible playbook export. Generates tasks using `ansible.builtin.script` and `ansible.builtin.copy` to apply the same modules on bare machines.
- **`bases/`** — Base VM type definitions for Vultr provisioning. Each subdirectory contains a `<id>.yaml` with `id`, `name`, `description`, `os`, `default_plan`, `packages`, `steps`, `disabled`, and `icon`. The `icon` field controls the badge shown on topology graph VM nodes — either a keyword string (`ubuntu`, `linux`, `debian`, `kali`, `windows`, `attacker`, `router`, `server`) or a dict `{svg_path: "M...", viewbox: "0 0 24 24"}` for a custom inline SVG path. Loaded by `builder/base_loader.py`; exposed at `GET /admin/base-types`.
- **`frontend/templates/`** — Jinja2 HTML templates. Dark theme admin panel, VM detail pages, network topology, Caldera dashboard, AI agent session pages.
- **`playbooks/`** — Ansible playbooks for Vultr VM lifecycle management. `create-vm.yml` provisions a Vultr VPS and optionally creates a Cloudflare DNS A record; `destroy-vm.yml` removes the instance and DNS record. `collections/requirements.yml` lists the `vultr.cloud` and `community.general` collections — Semaphore installs these automatically before running either playbook.

### Module System

Each module is a YAML file with: `id`, `name`, `type` (vulnerability/hardening/payload/application_external/application_internal/goal), `difficulty`, `points`, `category`, `script` (optional .sh), `verification` spec, `hints`, `conflicts`, `requires`. Optional resource fields: `min_ram_mb`, `min_vcpu` (used by `builder/plan_sizing.py` to auto-size Vultr plans when VM quota provisioning is active).

**`stage` field** (vulnerability and payload modules only): `preapplied` (default) — installed at build time, visible to blue team, scored when fixed. `caldera` — installed at build time but hidden from blue team; Caldera discovers and exploits these during red team operations. Stage does not affect module selection quotas.

Application modules install infrastructure that vulnerability/payload modules can target via `requires`. They award 0 points and are selected via their own quota keys (`"application_external"` or `"application_internal"` in `EVENT_QUOTA`). Payload modules are scored like vulnerabilities — users must find and remove malicious artifacts for points.

**Goal modules** (`type: goal`, lives in `modules/goals/`) represent red team objectives. Additional YAML fields: `red_points` (awarded to red team on achievement), `defend_points` (awarded to blue team on revert), `revert_verification` (detects blue team revert). Goal modules are never scored through the normal blue team flow — they track state via `VMGoal` records. Goal quota key is `"goal"` in `EVENT_QUOTA`. The selector processes goals after applications so `requires` dependencies on app modules are available.

Verification types: `file_permissions`, `file_contains`, `file_not_contains`, `service_running`, `package_installed`, `port_closed`, `flag_contents`, `password_not_default`, `password_changed`, `http_response`, `process_running`, `file_absent`, `file_hash_changed`, `cron_not_present`, `user_not_exists`.

The selector (`builder/selector.py`) runs in ordered phases: (1) application modules, (2) goal modules, (3) vulnerability/payload modules, (4) hardening modules — then (5) category quotas, (6) tag quotas. This ordering ensures goal `requires` dependencies on apps are satisfied. Category/tag counts are inclusive. Respects bidirectional conflict exclusions, auto-resolves dependencies. Quota validation lives in `builder/quota_validation.py`.

### Key Design Decisions

- **Admin bootstrap**: the first registration on a fresh database must supply `ADMIN_BOOTSTRAP_TOKEN`; only that account is granted `is_admin = True`.
- **Docker access**: production services use least-privilege Docker socket proxies. The API proxy permits the container restart needed by `POST /admin/caldera-setup`; the host socket is not mounted into the API or Dockhand containers.

### Multi-Event System

The platform supports multiple concurrent events, each with independent settings and leaderboards.

- **Event lifecycle**: `draft` → `open` → `stopped`. Draft events are invisible to users. Open events accept registration. Stopped events are archived with frozen leaderboards (verification blocked).
- **One event per user**: each user is bound to exactly one event via `User.event_id`. The event's quota drives module selection at registration time.
- **Event settings**: name, quota (JSON), vm_quota (JSON, optional), description, welcome message, and an automatically enforced time limit.
- **Admin CRUD**: `POST/GET/PUT/DELETE /admin/events/{id}`, plus `/start` and `/stop` actions. Events with assigned users cannot be deleted. Stopping or deleting an event also cleans up associated Caldera operations.
- **Public event listing**: `GET /api/events` returns open events (no auth required) for the registration form.
- **Scoreboard scoping**: `GET /api/scoreboard?event_id=X` returns per-event rankings. `GET /api/scoreboard/events` lists all non-draft events for the selector dropdown.
- **Legacy `open` column**: the `Event.open` boolean is kept in the schema for SQLite compatibility (no column drops) but superseded by the `status` field. All code uses `status`.

### Ansible Export

Generates Ansible playbooks that apply modules to existing machines or VMs.

- **Entry point**: `builder/ansible.py` → `generate_ansible_export(quota, export_id)` loads modules, selects via quota (reusing `select_modules`), renders `playbook.yml.j2`, and stages scripts/files into an export directory.
- **API endpoint**: `POST /admin/ansible-export` (admin-only). Accepts `{"quota": {...}}` or `{"event_id": N}`. Returns a zip file containing `playbook.yml` + `scripts/` + `files/`.
- **Script handling**: Uses `ansible.builtin.script` to run existing `.sh` scripts unchanged, and `ansible.builtin.copy` for file copy steps.
- **No Docker artifacts**: Flag, audit.py, state.json, and build_snapshot.py are excluded — those are Docker-specific verification concerns.
- **Output structure**: `ansible_exports/{export_id}/playbook.yml` + `scripts/{module_id}__{script}.sh` + `files/{module_id}__{filename}`.

### Caldera Integration

An optional red team emulation layer using MITRE Caldera. Generates a Caldera plugin containing exploit abilities for each vulnerability module, loads it into a running Caldera instance, and creates adversary operations. Supports both flat (single adversary) and multi-path (per-path adversaries) modes.

- **`POST /admin/caldera-export`** — Download the Caldera plugin as a zip (abilities YAML + adversary definition). Accepts `{"quota": {...}}` or `{"event_id": N}`. Manual alternative to `caldera-setup`.
- **`POST /admin/caldera-setup`** — Fully automated: generates the plugin, copies it to the shared bind mount, patches `local.yml` to enable the `ctf-exploit` plugin, restarts the Caldera container, waits for health, waits for an agent belonging to an active VM in the event (matched by IP, not just group membership), creates a "CTF Red Team Emulation" operation, and caches attack tree JSON on each VM in the event.
- **`builder/caldera.py`** — Generates the plugin: iterates selected modules, creates one ability YAML per module (recon + exploit steps). Two generation modes: `generate_caldera_export()` (flat, single "CTF Full Exploit Chain" adversary) and `generate_caldera_export_multi_path()` (per-path adversaries with shell-level skip logic using marker files at `/tmp/.ctf_phase_{N}`).
- **`builder/attack_tree.py`** — Builds attack tree DAGs from a VM's assigned modules. Nodes are modules with `caldera` metadata, tagged with kill chain phase (mapped from ATT&CK tactic). Edges come from `requires` dependencies and phase ordering. `extract_paths()` runs DFS from initial-access nodes to extract distinct attack paths (max 20, pruned by length). `serialize_tree()` outputs JSON for storage and frontend rendering. Goal modules appear as terminal nodes at `GOAL_PHASE = 8` (`is_goal=True`); paths now terminate at goal nodes.
- **Attack tree API**: `GET /admin/caldera/attack-tree/{vm_id}` returns the attack tree for a VM computed from its `VMModule` assignments. `GET /admin/caldera/operations/{op_id}?include_tree=true` annotates tree nodes with operation result statuses (succeeded/failed/skipped/pending).
- **Operations API**: `GET/POST/DELETE /admin/caldera/operations` for CRUD. `POST /admin/caldera/operations/cleanup-orphaned` — finds and deletes operations whose event no longer exists in the database. `GET /admin/caldera/vm-summary` for per-VM attack aggregates. `GET /admin/caldera/vm/{vm_id}/results` for VM-specific operation results. `GET /admin/caldera/scoreboard?event_id=N` — red vs blue scoreboard: per-team `blue_defensive` (completed preapplied module points), `blue_reactive` (goal reverts: `defend_points × defend_count`), `red_offensive` (goal achievements: `red_points × achievement_count`).
- **Event lifecycle integration**: `POST /admin/events/{id}/stop` and `DELETE /admin/events/{id}` automatically delete all Caldera operations in the `event-{id}` group to prevent orphaned operations.
- **Visual attack graph**: Interactive DAG visualization using elkjs (layout) + D3.js (rendering) via CDN. Nodes are color-coded by status (green=exploited, red=defended, gray=skipped, cyan=running). Rendered on the VM detail page and Caldera operation detail page via `attack_tree_partial.html`. Nodes are partitioned by kill chain phase to ensure correct column placement.
- **Kill chain phases**: infrastructure(-1) → initial-access(0) → execution(1) → persistence(2) → privilege-escalation(3) → credential-access(4) → collection(5) → impact(6) → command-and-control(7) → **goal(8)**. Modules can override via `phase_override` in their caldera YAML.
- **Caldera env vars** (internal container-to-container, set in docker-compose): `CALDERA_PLUGIN_DIR` (bind mount path, default `/caldera-plugin/ctf-exploit`), `CALDERA_CONFIG_PATH` (default `/caldera-config/local.yml`), `CALDERA_INTERNAL_URL` (default `http://ctf-caldera:8888`), `CALDERA_CONTAINER_NAME` (default `ctf-caldera`), `CALDERA_STARTUP_TIMEOUT` (seconds, default `120`).

### AI Red Team Agent

A PentestGPT-inspired autonomous red team agent that attacks VMs via Caldera operations with human-in-the-loop approval. Runs as a separate FastAPI service (`ai_agent/`) communicating with the main CTF API via HTTP.

**Architecture:**
- Separate container (`ai-agent` in docker-compose, profile: `ai-agent`) with its own SQLite database
- Main API proxies requests via `api/routes/ai_agent.py` (admin auth enforced at proxy layer)
- Agent service requires API key auth (`AGENT_API_KEY`) on all endpoints
- Consumes attack trees from `GET /admin/caldera/attack-tree/{vm_id}` at session creation

**Key components (`ai_agent/`):**
- `config.py` — Environment-based configuration (AI API, Caldera, CTF API, SSH, behavior)
- `db/models.py` — SQLAlchemy models: `AgentSession`, `AgentAction`, `AgentLog`, `StateEntity`
- `planner/egats.py` — EGATS planner (PentestGPT v2-inspired): UCB node selection, TDI difficulty scoring, promise backpropagation, branch pruning
- `planner/attack_tree.py` — Attack tree data structures with UCB scoring
- `planner/tda.py` — Task Difficulty Assessment (4 weighted dimensions: horizon, evidence, context, historical success)
- `memory/context.py` — Context assembly from attack tree path + state summaries; load estimation based on tree complexity
- `memory/state_store.py` — Persistent state for hosts, services, credentials, action results
- `tools/caldera.py` — Caldera integration: create operations, execute abilities (input sanitized with regex)
- `tools/ssh.py` — SSH command execution (disabled until key configured)
- `services/session_manager.py` — Session lifecycle, approval flow, step execution, result recording
- `services/auto_step.py` — Background auto-stepping for autonomous operation (optional)
- `llm/client.py` — OpenAI-compatible API client with structured JSON responses
- `routes/sessions.py` — Agent API endpoints with API key auth

**Planner algorithm (EGATS):**
1. Select next node via UCB score: `exploitation + exploration - difficulty_penalty`
2. Compute TDI (Task Difficulty Index) from node depth, evidence, context load, historical success
3. Choose mode: BFS (TDI > 0.6), DFS (TDI < 0.3), or hybrid
4. Prune branches with TDI > 0.8 after 3+ failed attempts
5. Query LLM with context (attack path, mode guidance, sibling summaries, progress)
6. LLM proposes action as JSON; system assesses risk level
7. Admin approves/rejects (or auto-approves if disabled)
8. Execute via Caldera; record result on target node; backpropagate promise scores

**Session flow:**
1. Admin creates session for event/VM at `/admin/ai-agent`
2. Agent fetches attack tree from CTF API (if VM specified)
3. Admin starts session → planner loads tree into memory
4. Admin clicks "Step" (or auto-step runs) → planner proposes action
5. With approval: admin reviews description/reasoning/risk → approves or rejects
6. Action executes via Caldera → result recorded → next step planned
7. Repeat until budget exhausted or admin stops

**API endpoints (proxied through main API):**
- `POST /admin/ai-agent/sessions` — create session (`{event_id, vm_id?, target_ip?, approval_required?}`)
- `GET /admin/ai-agent/sessions` — list sessions
- `GET /admin/ai-agent/sessions/{id}` — session detail (status, actions, attack tree state)
- `POST /admin/ai-agent/sessions/{id}/start` — start session
- `POST /admin/ai-agent/sessions/{id}/stop` — stop session
- `POST /admin/ai-agent/sessions/{id}/step` — plan next action
- `POST /admin/ai-agent/sessions/{id}/approve/{action_id}` — approve and execute
- `POST /admin/ai-agent/sessions/{id}/reject/{action_id}` — reject action
- `GET /admin/ai-agent/sessions/{id}/logs` — agent logs

**Admin UI:**
- `/admin/ai-agent` — session list + create form (event/VM selection, approval toggle)
- `/admin/ai-agent/session/{id}` — session detail: status panel, pending approvals with approve/reject buttons, attack tree visualization, recent actions, agent logs
- Live polling: session state every 5s, logs every 8s
- XSS protection: all LLM-generated content escaped via `esc()` function

**Environment variables:**
- `AI_API_BASE` — OpenAI-compatible API endpoint
- `AI_API_KEY` — AI provider API key
- `AI_MODEL` — model ID (default: `gpt-4o`)
- `AGENT_API_KEY` — shared key for CTF API ↔ agent communication
- `CTF_API_KEY` — agent's key for calling CTF API (required for VM targeting)
- `AGENT_MAX_STEPS` — step budget per session (default: 100)
- `AGENT_APPROVAL_REQUIRED` — require human approval (default: `true`)
- `AGENT_AUTO_STEP` — enable background auto-stepping (default: `false`)
- `AGENT_AUTO_STEP_INTERVAL` — seconds between auto-steps (default: 30)

**Security:**
- Agent service requires API key auth on all endpoints
- Main API enforces admin auth before proxying
- All LLM-generated inputs sanitized before Caldera API calls
- XSS protection in templates (textContent-based escaping)
- Agent container uses minimal dependencies (`requirements-agent.txt`)
- Agent does not share main API secrets (explicit env vars only)

**Run:** `docker compose --profile ai-agent up -d`

### Vulnerability Stages & Goal Objectives

Red vs blue scoring layer built on top of the Caldera integration.

**`stage` field** distinguishes two classes of vulnerability/payload modules:
- `preapplied` (default) — blue team sees, fixes, and earns `points`. Included in Caldera attack tree if the module has `caldera` metadata.
- `caldera` — hidden from blue team dashboard, hints, and scoring. Caldera discovers and exploits these. Set `points: 0` by convention (not enforced). Stage is stored on `VMModule.stage` and propagated from YAML at assignment time.

**Goal modules** (`type: goal`, `modules/goals/`) are terminal red team objectives (deface website, install C2 beacon, exfiltrate `/etc/shadow`). They sit at phase 8 in the attack tree as terminal nodes. Key YAML fields:

| Field | Description |
|-------|-------------|
| `red_points` | Awarded to red team each time the goal is achieved |
| `defend_points` | Awarded to blue team each time the goal is reverted |
| `verification` | Detects goal was achieved (e.g. `http_response` body_contains) |
| `revert_verification` | Detects blue team reverted it |

**Goal state machine** (per VM, cyclical): `pending → achieved → defended → achieved → ...`

Each achievement increments `VMGoal.achievement_count`; each defence increments `VMGoal.defend_count`. Blue team is incentivised to fix root causes rather than just revert symptoms — Caldera can re-exploit on the next check cycle.

**VMGoal API** (`api/routes/vm_goals.py`):
- `GET /admin/vms/{vm_id}/goals` — list goal states for a VM
- `POST /admin/vms/{vm_id}/goals/{goal_id}/check` — run `verification` + `revert_verification` against a VM and transition state. Remote checks support `http_response`, `service_running`, `file_exists`, and `file_absent`; SSH checks use the platform key and a per-VM pinned host key.

**Scoring model**:
- Blue defensive: sum of `points` for completed `preapplied` VMModules (unchanged from existing flow)
- Blue reactive: `defend_points × defend_count` per VMGoal
- Red offensive: `red_points × achievement_count` per VMGoal
- Scoreboard: `GET /admin/caldera/scoreboard?event_id=N`

### Semaphore Integration

Ansible Semaphore is used to provision VMs by running the module playbook remotely. The client lives in `api/services/semaphore.py`.

- **`SEMAPHORE_URL`** — Base URL of the Semaphore instance (default `http://ctf-semaphore:3000`).
- **`SEMAPHORE_ADMIN`** / **`SEMAPHORE_ADMIN_PASSWORD`** — Semaphore admin credentials.
- Semaphore project IDs are stored on the `Event` model (`semaphore_project_id`, `semaphore_key_id`) and reused across VM provisions for the same event.
- Per-VM provisioning state is tracked in `VM.provision_step`, `VM.provision_error`, `VM.semaphore_project_id`, `VM.semaphore_task_id`, and `VM.agent_status`.

### Vultr VM Provisioning

When `VULTR_API_KEY` is set, admins can create and destroy Vultr VPS instances directly from the admin panel. The flow runs through Semaphore (localhost playbooks, `connection: local`).

**Create flow:**
1. Admin submits "Create on Vultr" form (hostname, team, OS, plan)
2. API creates a `VM` record with `status="creating"` and kicks off `_run_vultr_create()` as a background task
3. Background task stages `playbooks/create-vm.yml` + `collections/requirements.yml` to a temp dir in `/shared/playbooks/`
4. Creates a Semaphore localhost project/inventory/repo/template, passes extra vars (hostname, plan, OS, region, SSH key, Cloudflare token) via template arguments
5. Runs the Semaphore task and polls until complete
6. Parses `VULTR_RESULT={"ip": "...", "vultr_id": "...", "dns_record_id": "..."}` from task output
7. Updates `VM` with IP, `vultr_id`, `cloudflare_record_id`; sets `status="registered"`

**Destroy flow:** `POST /admin/vms/{id}/destroy-vultr` → stages `destroy-vm.yml` → runs via Semaphore → deletes VM record on success.

**Status polling:** `GET /admin/vms/{vm_id}/create-status` returns `provision_step`, `provision_error`, and current IP. The VM detail page polls this every 4 s while `status === "creating"`.

**Reference endpoints:**
- `GET /admin/vultr/plans` — proxies Vultr API, returns vc2 plan list for OS/plan dropdowns
- `GET /admin/vultr/os` — proxies Vultr API, returns OS list

**Secrets:** `VULTR_API_KEY` is injected into the Semaphore container environment (docker-compose); all other secrets (Cloudflare token, SSH public key) are passed as Semaphore template extra vars at runtime.

### VM Quota System

An event-level quota system for automated VM provisioning, analogous to the module quota. Admins define VM types and counts on an event; when the event starts, the platform auto-creates Vultr VMs for every team, assigns modules, sizes plans, and deploys everything.

**VM quota JSON schema** (`Event.vm_quota`):
```json
{
  "ubuntu_target": {
    "os": "Ubuntu 24.04 LTS x64",
    "default_plan": "vc2-1c-2gb",
    "count": 3,
    "role": "target",
    "region": "ewr"
  },
  "red_team": {
    "os": "Ubuntu 24.04 LTS x64",
    "default_plan": "vc2-2c-4gb",
    "count": 1,
    "role": "attacker"
  }
}
```

- **Roles**: `"target"` VMs get modules assigned (via `select_modules()`) and provisioned via Ansible. `"attacker"` VMs are bare OS only (no modules), marked active immediately after Vultr creation.
- **Plan sizing**: Modules can declare `min_ram_mb` and `min_vcpu` in their YAML. `builder/plan_sizing.py` → `plan_for_vm()` picks the cheapest Vultr plan meeting `max(default_plan resources, module resource sum)`.
- **Validation**: `builder/vm_quota_validation.py` → `validate_vm_quota()` enforces slug keys, required fields (`os`, `default_plan`, `count`, `role`), and valid role values.

**Auto-provisioning flow** (triggered by `POST /admin/events/{id}/start` when `vm_quota` is defined):
1. Validates teams exist for the event.
2. Pre-creates a Semaphore project + SSH key for the event (avoids race conditions across concurrent threads).
3. For each team × VM type × count: creates VM record, assigns modules (target role only), sizes Vultr plan.
4. Spawns one `_run_vultr_create()` thread per VM (concurrent).
5. After Vultr creation completes, target VMs auto-chain into `_run_provision()` (module deployment via Ansible). Attacker VMs go directly to `status="active"`.
6. Returns `{"status": "started", "provisioning": true, "vm_count": N}`.

**Provisioning dashboard**: `GET /admin/events/{id}/provision-status` returns aggregate progress (`{total, creating, registered, provisioning, active, failed, vms: [...]}`). The admin UI polls this every 5 seconds and shows a real-time progress bar, per-VM status table, and "Retry Failed" button.

**Admin UI**: The event form has a "VM Quota" section with a dynamic form editor (type name, OS dropdown, plan dropdown, count, role, region) and a raw JSON toggle. Starting an event with vm_quota shows a confirmation dialog with VM count and estimated monthly cost.

**Backward compatibility**: Events without `vm_quota` start as before — the `if event.vm_quota:` guard in `start_event` ensures no behavior change.

### Team and VM Management

The platform uses team-scoped VMs. Admins can provision new VPS instances directly from the admin UI ("Create on Vultr") or register existing machines manually ("Register Existing").

- **Team**: groups of users within an event. `Team` has `name`, `event_id` FK. Admin CRUD at `GET/POST /admin/teams`, `PUT/DELETE /admin/teams/{id}`. Teams cannot be deleted while they have VMs.
- **VM**: a registered target machine. Fields: `hostname`, `ip_address`, `os`, `status` (registered/active/stopped/failed), `ssh_port`, `ssh_user`, `notes`, `team_id`, `event_id` (denormalized from team for query convenience), timestamps. Admin CRUD at `/admin/vms` and `/admin/vms/{id}`.
- **VMModule**: mirrors `UserModule` — tracks which modules are assigned to a VM and their completion status. Created via `POST /admin/vms/{id}/assign-modules` (runs `select_modules()` with the event's quota) or manually via `POST /admin/vms/{id}/add-module`.
- **VM-scoped Ansible export**: `POST /admin/vms/{id}/ansible-export` generates a playbook from the VM's assigned modules (reuses `render_playbook` + `_stage_files` from `builder/ansible.py`). Returns a zip download.
- **Admin UI**: the admin page has a "Teams & VMs" card with inline create forms and overview tables. Each VM links to `/admin/vm/{id}` — a dedicated detail page showing connection info (with copyable SSH command), module progress, status/notes editing, and action buttons.

### Network Topology

An interactive D3.js force-directed graph at `/admin/topology` that visualizes the event → team → VM hierarchy as a network map. Accessible from the "Network Topology" button in the admin page's Teams & VMs card.

- **Node hierarchy**: Event nodes (large cyan circles, center) → Team nodes (medium colored circles) → VM nodes (rounded-square server rack icons with OS badge).
- **VM node icons**: Hybrid style — monoline server rack SVG (3 stacked rectangles with LED dots) plus a small circular icon badge in the bottom-right corner. The badge icon is resolved from the base type's `icon` field (see Base Types below): keyword strings map to built-in SVG paths (`ubuntu`, `linux`, `debian`, `kali`, `windows`, `attacker`, `router`, `server`); custom SVG paths can be specified inline via `{svg_path, viewbox}`. Falls back to OS-string matching for VMs without a `base_type`. Node border/glow color reflects status: green (active), amber (creating/provisioning), red (failed), grey (stopped/registered).
- **Interactions**: Drag any node to reposition, scroll to zoom, drag background to pan, hover for tooltip (IP, OS, module progress bar), right-click for context menu (View Details, Provision, Assign Modules, Export Playbook, Destroy for VMs; View Team, Delete Team for teams), double-click to navigate to detail page.
- **Live polling**: Fetches `GET /admin/topology-data` every 5 seconds. Status changes animate with smooth color transitions and a pulse effect. New nodes fade in, removed nodes fade out.
- **Event filter**: Dropdown in toolbar scopes the graph to a single event or shows all non-draft events.
- **API endpoint**: `GET /admin/topology-data?event_id=X` (admin-only). Returns `{ nodes: [...], links: [...] }` — each node has `id`, `type` (event/team/vm), `label`, `status`, and type-specific fields (IP, OS, module counts for VMs; color for teams). Links connect event→team and team→VM.
- **D3 dependency**: D3 v7 loaded from CDN only on the topology page. Chosen over higher-level graph libraries (Cytoscape, Vis.js) because D3 will be reused elsewhere in the project.

### Database Models (api/models.py)

Seven models: `User` (with `event_id` FK to Event), `Event` (name, quota JSON, `vm_quota` JSON, status, description, welcome_message, time_limit_minutes, `semaphore_project_id`, `semaphore_key_id`), `Team` (name, event_id), `VM` (connection info, status, team_id, event_id, `vm_type` — traces which vm_quota entry created this VM; provisioning state: `provision_step`, `provision_error`, `semaphore_project_id`, `semaphore_task_id`, `agent_status`; Vultr cloud fields: `vultr_id`, `vultr_plan`, `vultr_region`; Cloudflare: `cloudflare_record_id`; Caldera: `attack_tree_json` — cached serialized attack tree with generation timestamp), `VMModule` (completion tracking per module per VM; `stage` column: `"preapplied"`, `"caldera"`, or `null` for non-vuln types), `VMGoal` (tracks cyclical red team goal state per VM: `status` pending/achieved/defended, `achievement_count`, `defend_count`, `red_points`, `defend_points`, `achieved_at`, `defended_at`), `PlatformSettings` (key-value store for platform-wide config). A default "open" event is created at startup if none exists.

### Agent Database Models (ai_agent/db/models.py)

Four models in a separate SQLite database: `AgentSession` (session lifecycle, CTF event/VM references, attack tree JSON, approval settings), `AgentAction` (proposed actions with type, risk level, description, reasoning, target_node_id for result tracking, execution state), `AgentLog` (reasoning/activity logs with component, level, metadata), `StateEntity` (persistent state: hosts, services, credentials, action results).

## Production Deployment

Full production stack lives in `deploy/`: Traefik reverse proxy + Dockhand container UI + CTF API + MITRE Caldera + Ansible Semaphore.

### Deployment Steps

1. Copy project to server at `/opt/ctf-it`
2. Configure root `.env` (see `.env.example`) — SECRET_KEY, ADMIN_BOOTSTRAP_TOKEN, DATA_ENCRYPTION_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_DOMAIN
3. Configure `deploy/.env` (see `deploy/.env.example`) — DOMAIN, ACME_EMAIL, Semaphore credentials, CALDERA_AGENT_URL
4. Create `deploy/caldera/config/local.yml` from `local.yml.example` with generated secrets
5. Create Cloudflare DNS A records for subdomains: `ctf`, `caldera`, `semaphore`, `dockhand`, `traefik` → server IP
6. Run `docker compose up -d` from `deploy/`

### CRITICAL: Let's Encrypt Rate Limits

**Let's Encrypt hard limit: 5 certificates per week per exact domain.** This is per-domain, NOT per-account. Changing ACME_EMAIL or deleting acme.json does NOT reset this limit.

If you hit `too many certificates (5) already issued for this exact set of identifiers`:
- **DO NOT** try changing ACME accounts, deleting acme.json, or waiting for authorization failure rate limits
- **MUST** use different subdomains (e.g., `ctf1.ye-et.com` instead of `ctf.ye-et.com`)
- To switch subdomains: update all `Host(\`...\`)` labels in `deploy/docker-compose.yml`, create new DNS records, delete `deploy_ctf-letsencrypt` volume acme.json, restart Traefik

Subdomain patterns in `deploy/docker-compose.yml`:
- `ctf.${DOMAIN}` → CTF API
- `caldera.${DOMAIN}` → Caldera
- `semaphore.${DOMAIN}` → Semaphore
- `dockhand.${DOMAIN}` → Dockhand
- `traefik.${DOMAIN}` → Traefik dashboard

### Service URLs (once deployed)

- `https://ctf1.ye-et.com` — CTF API & Admin Panel
- `https://caldera1.ye-et.com` — MITRE Caldera C2
- `https://semaphore1.ye-et.com` — Ansible Semaphore
- `https://dockhand1.ye-et.com` — Container Management
- `https://traefik1.ye-et.com` — Traefik Dashboard
