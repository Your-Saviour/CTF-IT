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

## Architecture

### Request Flow

User selects an open event and registers → user is bound to that event (`User.event_id`) → async background task builds Docker image using the event's quota → image pushed to local Docker registry → dashboard polls `/api/images/status` until ready → user pulls image from registry and runs container → fixes vulns → runs `audit.py` inside container → POSTs broad system snapshot to `/api/verify` → backend matches snapshot against user's assigned modules (server-side) → awards points. Scoreboard is scoped per-event.

### Key Components

- **`api/`** — FastAPI app serving both HTML templates (Jinja2) and JSON API endpoints. Routes split into `auth`, `images`, `verify`, `scoreboard`, `admin`, `ansible_export`, `vm` (Team/VM CRUD).
- **`builder/`** — Image build orchestration. `main.py` is the entry point: loads modules, selects per quota, renders Dockerfile from Jinja2 template, runs `docker build`, pushes to local registry, returns image tag + flag. `ansible.py` provides an alternative export path that generates Ansible playbooks instead of Docker images.
- **`modules/`** — Self-contained YAML definitions + optional shell scripts for vulnerabilities (`vulns/`), hardening tasks (`hardening/`), payloads (`payloads/`), external applications (`application_external/`), and internal applications (`application_internal/`). Adding a new module = adding a YAML + optional .sh file, no code changes needed.
- **`templates/Dockerfile.j2`** — Jinja2 template for user container images. Copies vuln scripts, runs them, bakes in flag and opaque state file.
- **`templates/playbook.yml.j2`** — Jinja2 template for Ansible playbook export. Generates tasks using `ansible.builtin.script` and `ansible.builtin.copy` to apply the same modules on bare machines.
- **`base/`** — Base Docker image (Ubuntu 22.04 + common tools). All user images inherit from `ctf-base:latest`.
- **`audit.py`** — Runs inside user containers. Performs a broad security audit (file permissions, configs, services, packages, ports, HTTP responses, processes, shadow hashes) and outputs a JSON system snapshot. Contains no module-specific logic — the server matches the snapshot against the user's assigned modules.
- **`frontend/templates/`** — Jinja2 HTML templates. Dark theme, client-side polling for build status.

### Module System

Each module is a YAML file with: `id`, `name`, `type` (vulnerability/hardening/payload/application_external/application_internal), `difficulty`, `points`, `category`, `script` (optional .sh), `verification` spec, `hints`, `conflicts`, `requires`.

Application modules install infrastructure that vulnerability/payload modules can target via `requires`. They award 0 points and are selected via their own quota keys (`"application_external"` or `"application_internal"` in `EVENT_QUOTA`). Payload modules are scored like vulnerabilities — users must find and remove malicious artifacts for points.

Verification types: `file_permissions`, `file_contains`, `file_not_contains`, `service_running`, `package_installed`, `port_closed`, `flag_contents`, `password_not_default`, `password_changed`, `http_response`, `process_running`, `file_absent`, `file_hash_changed`, `cron_not_present`, `user_not_exists`.

The selector (`builder/selector.py`) runs three phases: (1) type/difficulty quotas, (2) category quotas, (3) tag quotas. Category/tag counts are inclusive — modules already picked by earlier phases count toward later quotas. Respects bidirectional conflict exclusions, auto-resolves dependencies, and counts dependency-pulled modules toward their type/difficulty quota. Quota validation lives in `builder/quota_validation.py`.

### Key Design Decisions

- **Deterministic flags**: `HMAC(secret_key, user_id)` — same user always gets same flag, enables rebuilds without storing flags separately.
- **Opaque collection**: the container ships only a broad `audit.py` and a minimal `state.json` (user_id + build-time snapshots). No module names, verification specs, or expected values are exposed to the user. The server knows which modules are assigned via the `UserModule` table and extracts relevant data from the broad snapshot.
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
- **Event settings**: name, quota (JSON), description, welcome message, time limit (display-only; enforcement is manual via start/stop).
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

### Team and VM Management

The platform supports VM-based deployments alongside the existing per-user Docker flow. VMs are team-scoped (not per-user) and will eventually be auto-provisioned; for now admins register them manually.

- **Team**: groups of users within an event. `Team` has `name`, `event_id` FK. Admin CRUD at `GET/POST /admin/teams`, `PUT/DELETE /admin/teams/{id}`. Teams cannot be deleted while they have VMs.
- **VM**: a registered target machine. Fields: `hostname`, `ip_address`, `os`, `status` (registered/active/stopped/failed), `ssh_port`, `ssh_user`, `notes`, `team_id`, `event_id` (denormalized from team for query convenience), timestamps. Admin CRUD at `/admin/vms` and `/admin/vms/{id}`.
- **VMModule**: mirrors `UserModule` — tracks which modules are assigned to a VM and their completion status. Created via `POST /admin/vms/{id}/assign-modules` (runs `select_modules()` with the event's quota) or manually via `POST /admin/vms/{id}/add-module`.
- **VM-scoped Ansible export**: `POST /admin/vms/{id}/ansible-export` generates a playbook from the VM's assigned modules (reuses `render_playbook` + `_stage_files` from `builder/ansible.py`). Returns a zip download.
- **Admin UI**: the admin page has a "Teams & VMs" card with inline create forms and overview tables. Each VM links to `/admin/vm/{id}` — a dedicated detail page showing connection info (with copyable SSH command), module progress, status/notes editing, and action buttons.

### Database Models (api/models.py)

Seven models: `User` (with `event_id` FK to Event), `UserImage` (build status: queued→building→ready→failed), `UserModule` (completion tracking per module per user), `Event` (name, quota JSON, status, description, welcome_message, time_limit_minutes), `Team` (name, event_id), `VM` (connection info, status, team_id, event_id), `VMModule` (mirrors UserModule — completion tracking per module per VM). A default "open" event is created at startup if none exists.
