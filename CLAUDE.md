# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CTF training platform where each user gets a uniquely generated Docker container with randomized vulnerabilities and hardening tasks. Users fix issues locally, then submit verification from inside the container. The platform handles image generation, distribution, verification, and scoring.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn api.main:app --reload

# Run with Docker Compose (production)
docker compose up -d

# Build base image (required before user images can be built)
docker build --build-arg "$(cat base/.env)" -t ctf-base:latest base/
```

### Testing

See [TEST_PLAN.md](TEST_PLAN.md) for the full end-to-end integration test, or run the automated script:

```bash
docker compose down -v && tests/e2e_test.sh
```

- `.env.test` is checked into the repo with a quota that selects all 10 modules (9 scored + 1 app) — the e2e script copies it to `.env` automatically
- `ROOT_PASSWORD` in `base/.env` is `changeme123` — this is the known default the `password_changed` verification checks against
- Always test via the API docker container (`docker compose`), not by importing Python modules directly — the builder needs Docker socket access
- Containers require `--privileged --cgroupns=private` for systemd to start (Docker only grants rw cgroup access in privileged mode)

### Required Environment Variables

See `.env.example` for full documentation. Key variables:

- `SECRET_KEY` — used for session signing and deterministic flag generation (HMAC)
- `DATABASE_URL` — defaults to `sqlite:///ctf.db`, use postgres URI for production
- `EVENT_QUOTA` — JSON defining module selection counts per type/difficulty, with optional `categories` and `tags` keys for additional filtering (see `.env.example`)
- `REGISTRY_HOST` — user-facing address for the Docker registry (default `localhost:5050`). Set to LAN IP for remote access
- `REGISTRY_PUSH_HOST` — address the Docker daemon uses to push to the registry (default `localhost:5050`). Only change if running DinD
- `API_HOST` — address shown in the dashboard verify command (default `host.docker.internal:8080`). Set to server IP/domain for LAN/VPS
- `API_PORT` — port the API is exposed on (default `8080`)
- `DOCKER_PLATFORM` — target platform for image builds (e.g. `linux/arm64`). Set when build server and users have different architectures
- `SEMAPHORE_URL` / `SEMAPHORE_ADMIN` / `SEMAPHORE_ADMIN_PASSWORD` — Ansible Semaphore connection (internal service; defaults work with docker-compose stack)
- `VULTR_API_KEY` / `VULTR_DEFAULT_REGION` — Vultr cloud provisioning (optional; enables VM create/destroy from admin UI)
- `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_DOMAIN` — Cloudflare DNS (optional; auto-creates DNS A records for provisioned VMs)

## Architecture

### Request Flow

User selects an open event and registers → user is bound to that event (`User.event_id`) → async background task builds Docker image using the event's quota → image pushed to local Docker registry → dashboard polls `/api/images/status` until ready → user pulls image from registry and runs container → fixes vulns → runs `audit.py` inside container → POSTs broad system snapshot to `/api/verify` → backend matches snapshot against user's assigned modules (server-side) → awards points. Scoreboard is scoped per-event.

### Key Components

- **`api/`** — FastAPI app serving both HTML templates (Jinja2) and JSON API endpoints. Routes split into `auth`, `images`, `verify`, `scoreboard`, `admin`, `ansible_export`, `caldera_export`, `caldera_setup`, `caldera_ops`, `caldera_tree`, `vm` (Team/VM CRUD, topology data).
- **`builder/`** — Image build orchestration. `main.py` is the entry point: loads modules, selects per quota, renders Dockerfile from Jinja2 template, runs `docker build`, pushes to local registry, returns image tag + flag. `ansible.py` provides an alternative export path that generates Ansible playbooks instead of Docker images. `caldera.py` generates MITRE Caldera plugin exports (abilities + adversaries) from selected modules; supports both flat (single adversary) and multi-path (per-path adversaries with skip logic) modes. `attack_tree.py` builds directed acyclic graphs (attack trees) from a VM's assigned modules, ordered by ATT&CK kill chain phase, and extracts distinct attack paths via DFS. `vm_quota_validation.py` validates the vm_quota JSON schema. `plan_sizing.py` picks the cheapest Vultr plan that meets module resource requirements.
- **`modules/`** — Self-contained YAML definitions + optional shell scripts for vulnerabilities (`vulns/`), hardening tasks (`hardening/`), payloads (`payloads/`), external applications (`application_external/`), and internal applications (`application_internal/`). Adding a new module = adding a YAML + optional .sh file, no code changes needed.
- **`templates/Dockerfile.j2`** — Jinja2 template for user container images. Copies vuln scripts, runs them, bakes in flag and opaque state file.
- **`templates/playbook.yml.j2`** — Jinja2 template for Ansible playbook export. Generates tasks using `ansible.builtin.script` and `ansible.builtin.copy` to apply the same modules on bare machines.
- **`base/`** — Base Docker image (Ubuntu 22.04 + common tools). All user images inherit from `ctf-base:latest`.
- **`audit.py`** — Runs inside user containers. Performs a broad security audit (file permissions, configs, services, packages, ports, HTTP responses, processes, shadow hashes) and outputs a JSON system snapshot. Contains no module-specific logic — the server matches the snapshot against the user's assigned modules.
- **`frontend/templates/`** — Jinja2 HTML templates. Dark theme, client-side polling for build status.
- **`playbooks/`** — Ansible playbooks for Vultr VM lifecycle management. `create-vm.yml` provisions a Vultr VPS and optionally creates a Cloudflare DNS A record; `destroy-vm.yml` removes the instance and DNS record. `collections/requirements.yml` lists the `vultr.cloud` and `community.general` collections — Semaphore installs these automatically before running either playbook.

### Module System

Each module is a YAML file with: `id`, `name`, `type` (vulnerability/hardening/payload/application_external/application_internal), `difficulty`, `points`, `category`, `script` (optional .sh), `verification` spec, `hints`, `conflicts`, `requires`. Optional resource fields: `min_ram_mb`, `min_vcpu` (used by `builder/plan_sizing.py` to auto-size Vultr plans when VM quota provisioning is active).

Application modules install infrastructure that vulnerability/payload modules can target via `requires`. They award 0 points and are selected via their own quota keys (`"application_external"` or `"application_internal"` in `EVENT_QUOTA`). Payload modules are scored like vulnerabilities — users must find and remove malicious artifacts for points.

Verification types: `file_permissions`, `file_contains`, `file_not_contains`, `service_running`, `package_installed`, `port_closed`, `flag_contents`, `password_not_default`, `password_changed`, `http_response`, `process_running`, `file_absent`, `file_hash_changed`, `cron_not_present`, `user_not_exists`.

The selector (`builder/selector.py`) runs three phases: (1) type/difficulty quotas, (2) category quotas, (3) tag quotas. Category/tag counts are inclusive — modules already picked by earlier phases count toward later quotas. Respects bidirectional conflict exclusions, auto-resolves dependencies, and counts dependency-pulled modules toward their type/difficulty quota. Quota validation lives in `builder/quota_validation.py`.

### Key Design Decisions

- **Deterministic flags**: `HMAC(secret_key, user_id)` — same user always gets same flag, enables rebuilds without storing flags separately.
- **Opaque collection**: the container ships only a broad `audit.py` and a minimal `state.json` (user_id + build-time snapshots + `hash_paths`/`check_paths` file lists for payload verification). No module names, verification specs, or expected values are exposed to the user. The server knows which modules are assigned via the `UserModule` table and extracts relevant data from the broad snapshot.
- **Opaque verify response**: `/api/verify` only returns details (module ID, name) for **completed** challenges. Unsolved modules are hidden — the response includes only summary counts (`completed`, `remaining`, `newly_completed`) to prevent leaking task names.
- **Stateless verification**: flag in payload proves container legitimacy; no session required for verify endpoint.
- **In-process async builds**: uses `asyncio.create_task` (not a separate worker). Production spec calls for RQ + Redis but this is not yet implemented.
- **Local Docker registry**: a `registry:2` sidecar in docker-compose serves built images on port 5050. After build, images are tagged and pushed to the registry, then cleaned from the local daemon. Users `docker pull` from the registry. The push target is `localhost:5050` (not the compose service name) because the Docker daemon runs on the host via socket mount.
- **Docker socket required**: builder needs `/var/run/docker.sock` mounted to build images and push to the registry.
- **Auto-admin bootstrap**: the first user to register on a fresh database is automatically granted `is_admin = True`. This removes the need to run `promote_admin.py` after initial deployment. Subsequent registrations are unaffected.

### Multi-Event System

The platform supports multiple concurrent events, each with independent settings and leaderboards.

- **Event lifecycle**: `draft` → `open` → `stopped`. Draft events are invisible to users. Open events accept registration. Stopped events are archived with frozen leaderboards (verification blocked).
- **One event per user**: each user is bound to exactly one event via `User.event_id`. The event's quota drives module selection at registration time.
- **Event settings**: name, quota (JSON), vm_quota (JSON, optional), description, welcome message, time limit (display-only; enforcement is manual via start/stop).
- **Admin CRUD**: `POST/GET/PUT/DELETE /admin/events/{id}`, plus `/start` and `/stop` actions. Events with assigned users cannot be deleted.
- **Public event listing**: `GET /api/events` returns open events (no auth required) for the registration form.
- **Scoreboard scoping**: `GET /api/scoreboard?event_id=X` returns per-event rankings. `GET /api/scoreboard/events` lists all non-draft events for the selector dropdown.
- **Legacy `open` column**: the `Event.open` boolean is kept in the schema for SQLite compatibility (no column drops) but superseded by the `status` field. All code uses `status`.

### Ansible Export

An alternative to Docker image builds — generates Ansible playbooks that apply the same modules to bare machines or VMs.

- **Entry point**: `builder/ansible.py` → `generate_ansible_export(quota, export_id)` loads modules, selects via quota (reusing `select_modules`), renders `playbook.yml.j2`, and stages scripts/files into an export directory.
- **API endpoint**: `POST /admin/ansible-export` (admin-only). Accepts `{"quota": {...}}` or `{"event_id": N}`. Returns a zip file containing `playbook.yml` + `scripts/` + `files/`.
- **Script handling**: Uses `ansible.builtin.script` to run existing `.sh` scripts unchanged, and `ansible.builtin.copy` for file copy steps.
- **No Docker artifacts**: Flag, audit.py, state.json, and build_snapshot.py are excluded — those are Docker-specific verification concerns.
- **Output structure**: `ansible_exports/{export_id}/playbook.yml` + `scripts/{module_id}__{script}.sh` + `files/{module_id}__{filename}`.

### Caldera Integration

An optional red team emulation layer using MITRE Caldera. Generates a Caldera plugin containing exploit abilities for each vulnerability module, loads it into a running Caldera instance, and creates adversary operations. Supports both flat (single adversary) and multi-path (per-path adversaries) modes.

- **`POST /admin/caldera-export`** — Download the Caldera plugin as a zip (abilities YAML + adversary definition). Accepts `{"quota": {...}}` or `{"event_id": N}`. Manual alternative to `caldera-setup`.
- **`POST /admin/caldera-setup`** — Fully automated: generates the plugin, copies it to the shared bind mount, patches `local.yml` to enable the `ctf-exploit` plugin, restarts the Caldera container, waits for health, creates a "CTF Red Team Emulation" operation, and caches attack tree JSON on each VM in the event.
- **`builder/caldera.py`** — Generates the plugin: iterates selected modules, creates one ability YAML per module (recon + exploit steps). Two generation modes: `generate_caldera_export()` (flat, single "CTF Full Exploit Chain" adversary) and `generate_caldera_export_multi_path()` (per-path adversaries with shell-level skip logic using marker files at `/tmp/.ctf_phase_{N}`).
- **`builder/attack_tree.py`** — Builds attack tree DAGs from a VM's assigned modules. Nodes are modules with `caldera` metadata, tagged with kill chain phase (mapped from ATT&CK tactic). Edges come from `requires` dependencies and phase ordering. `extract_paths()` runs DFS from initial-access nodes to extract distinct attack paths (max 20, pruned by length). `serialize_tree()` outputs JSON for storage and frontend rendering.
- **Attack tree API**: `GET /admin/caldera/attack-tree/{vm_id}` returns the attack tree for a VM computed from its `VMModule` assignments. `GET /admin/caldera/operations/{op_id}?include_tree=true` annotates tree nodes with operation result statuses (succeeded/failed/skipped/pending).
- **Operations API**: `GET/POST/DELETE /admin/caldera/operations` for CRUD. `GET /admin/caldera/vm-summary` for per-VM attack aggregates. `GET /admin/caldera/vm/{vm_id}/results` for VM-specific operation results.
- **Visual attack graph**: Interactive DAG visualization using elkjs (layout) + D3.js (rendering) via CDN. Nodes are color-coded by status (green=exploited, red=defended, gray=skipped, cyan=running). Rendered on the VM detail page and Caldera operation detail page via `attack_tree_partial.html`. Nodes are partitioned by kill chain phase to ensure correct column placement.
- **Kill chain phases**: infrastructure(-1) → initial-access(0) → execution(1) → persistence(2) → privilege-escalation(3) → credential-access(4) → collection(5) → impact(6) → command-and-control(7). Modules can override via `phase_override` in their caldera YAML.
- **Caldera env vars** (internal container-to-container, set in docker-compose): `CALDERA_PLUGIN_DIR` (bind mount path, default `/caldera-plugin/ctf-exploit`), `CALDERA_CONFIG_PATH` (default `/caldera-config/local.yml`), `CALDERA_INTERNAL_URL` (default `http://ctf-caldera:8888`), `CALDERA_CONTAINER_NAME` (default `ctf-caldera`), `CALDERA_STARTUP_TIMEOUT` (seconds, default `120`).

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

The platform supports VM-based deployments alongside the existing per-user Docker flow. VMs are team-scoped (not per-user). Admins can provision new VPS instances directly from the admin UI ("Create on Vultr") or register existing machines manually ("Register Existing").

- **Team**: groups of users within an event. `Team` has `name`, `event_id` FK. Admin CRUD at `GET/POST /admin/teams`, `PUT/DELETE /admin/teams/{id}`. Teams cannot be deleted while they have VMs.
- **VM**: a registered target machine. Fields: `hostname`, `ip_address`, `os`, `status` (registered/active/stopped/failed), `ssh_port`, `ssh_user`, `notes`, `team_id`, `event_id` (denormalized from team for query convenience), timestamps. Admin CRUD at `/admin/vms` and `/admin/vms/{id}`.
- **VMModule**: mirrors `UserModule` — tracks which modules are assigned to a VM and their completion status. Created via `POST /admin/vms/{id}/assign-modules` (runs `select_modules()` with the event's quota) or manually via `POST /admin/vms/{id}/add-module`.
- **VM-scoped Ansible export**: `POST /admin/vms/{id}/ansible-export` generates a playbook from the VM's assigned modules (reuses `render_playbook` + `_stage_files` from `builder/ansible.py`). Returns a zip download.
- **Admin UI**: the admin page has a "Teams & VMs" card with inline create forms and overview tables. Each VM links to `/admin/vm/{id}` — a dedicated detail page showing connection info (with copyable SSH command), module progress, status/notes editing, and action buttons.

### Network Topology

An interactive D3.js force-directed graph at `/admin/topology` that visualizes the event → team → VM hierarchy as a network map. Accessible from the "Network Topology" button in the admin page's Teams & VMs card.

- **Node hierarchy**: Event nodes (large cyan circles, center) → Team nodes (medium colored circles) → VM nodes (rounded-square server rack icons with OS badge).
- **VM node icons**: Hybrid style — monoline server rack SVG (3 stacked rectangles with LED dots) plus a small circular OS badge in the bottom-right corner (penguin for Linux, grid for Windows, `>` for unknown). Node border/glow color reflects status: green (active), amber (creating/provisioning), red (failed), grey (stopped/registered).
- **Interactions**: Drag any node to reposition, scroll to zoom, drag background to pan, hover for tooltip (IP, OS, module progress bar), right-click for context menu (View Details, Provision, Assign Modules, Export Playbook, Destroy for VMs; View Team, Delete Team for teams), double-click to navigate to detail page.
- **Live polling**: Fetches `GET /admin/topology-data` every 5 seconds. Status changes animate with smooth color transitions and a pulse effect. New nodes fade in, removed nodes fade out.
- **Event filter**: Dropdown in toolbar scopes the graph to a single event or shows all non-draft events.
- **API endpoint**: `GET /admin/topology-data?event_id=X` (admin-only). Returns `{ nodes: [...], links: [...] }` — each node has `id`, `type` (event/team/vm), `label`, `status`, and type-specific fields (IP, OS, module counts for VMs; color for teams). Links connect event→team and team→VM.
- **D3 dependency**: D3 v7 loaded from CDN only on the topology page. Chosen over higher-level graph libraries (Cytoscape, Vis.js) because D3 will be reused elsewhere in the project.

### Database Models (api/models.py)

Eight models: `User` (with `event_id` FK to Event), `UserImage` (build status: queued→building→ready→failed), `UserModule` (completion tracking per module per user), `Event` (name, quota JSON, `vm_quota` JSON, status, description, welcome_message, time_limit_minutes, `semaphore_project_id`, `semaphore_key_id`), `Team` (name, event_id), `VM` (connection info, status, team_id, event_id, `vm_type` — traces which vm_quota entry created this VM; provisioning state: `provision_step`, `provision_error`, `semaphore_project_id`, `semaphore_task_id`, `agent_status`; Vultr cloud fields: `vultr_id`, `vultr_plan`, `vultr_region`; Cloudflare: `cloudflare_record_id`; Caldera: `attack_tree_json` — cached serialized attack tree with generation timestamp), `VMModule` (mirrors UserModule — completion tracking per module per VM), `PlatformSettings` (key-value store for platform-wide config). A default "open" event is created at startup if none exists.
