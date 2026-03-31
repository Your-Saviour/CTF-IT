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
docker build -t ctf-base:latest base/
```

### Testing

See [TEST_PLAN.md](TEST_PLAN.md) for the full end-to-end integration test.

- `.env.test` has test credentials with a quota that selects all modules — copy it to `.env` before testing
- `ROOT_PASSWORD` in `base/.env` is `changeme123` — this is the known default the `password_changed` verification checks against
- Always test via the API docker container (`docker compose`), not by importing Python modules directly — the builder needs Docker socket access

### Required Environment Variables

- `SECRET_KEY` — used for session signing and deterministic flag generation (HMAC)
- `DATABASE_URL` — defaults to `sqlite:///ctf.db`, use postgres URI for production
- `EVENT_QUOTA` — JSON defining module selection counts per type/difficulty, e.g. `{"vulnerability":{"easy":1,"medium":0,"hard":0},"hardening":{"easy":0,"medium":1,"hard":0}}`
- `REGISTRY_HOST` — user-facing address for the Docker registry (default `localhost:5050`). Set to LAN IP for remote access (e.g. `192.168.1.50:5050`)
- `REGISTRY_PUSH_HOST` — address the Docker daemon uses to push to the registry (default `localhost:5050`). Only change if running DinD instead of socket-mounted

## Architecture

### Request Flow

User registers → async background task builds Docker image with selected modules → image pushed to local Docker registry → dashboard polls `/api/images/status` until ready → user pulls image from registry and runs container → fixes vulns → runs `audit.py` inside container → POSTs broad system snapshot to `/api/verify` → backend matches snapshot against user's assigned modules (server-side) → awards points.

### Key Components

- **`api/`** — FastAPI app serving both HTML templates (Jinja2) and JSON API endpoints. Routes split into `auth`, `images`, `verify`, `scoreboard`, `admin`.
- **`builder/`** — Image build orchestration. `main.py` is the entry point: loads modules, selects per quota, renders Dockerfile from Jinja2 template, runs `docker build`, pushes to local registry, returns image tag + flag.
- **`modules/`** — Self-contained YAML definitions + optional shell scripts for vulnerabilities (`vulns/`) and hardening tasks (`hardening/`). Adding a new module = adding a YAML + optional .sh file, no code changes needed.
- **`templates/Dockerfile.j2`** — Jinja2 template for user container images. Copies vuln scripts, runs them, bakes in flag and opaque state file.
- **`base/`** — Base Docker image (Ubuntu 22.04 + common tools). All user images inherit from `ctf-base:latest`.
- **`audit.py`** — Runs inside user containers. Performs a broad security audit (file permissions, configs, services, packages, ports, shadow hashes) and outputs a JSON system snapshot. Contains no module-specific logic — the server matches the snapshot against the user's assigned modules.
- **`frontend/templates/`** — Jinja2 HTML templates. Dark theme, client-side polling for build status.

### Module System

Each module is a YAML file with: `id`, `name`, `type` (vulnerability/hardening), `difficulty`, `points`, `category`, `script` (optional .sh), `verification` spec, `hints`, `conflicts`, `requires`.

Verification types: `file_permissions`, `file_contains`, `file_not_contains`, `service_running`, `package_installed`, `port_closed`, `flag_contents`, `password_not_default`, `password_changed`.

The selector (`builder/selector.py`) respects conflict exclusions and auto-resolves dependencies.

### Key Design Decisions

- **Deterministic flags**: `HMAC(secret_key, user_id)` — same user always gets same flag, enables rebuilds without storing flags separately.
- **Opaque collection**: the container ships only a broad `audit.py` and a minimal `state.json` (user_id + build-time snapshots). No module names, verification specs, or expected values are exposed to the user. The server knows which modules are assigned via the `UserModule` table and extracts relevant data from the broad snapshot.
- **Stateless verification**: flag in payload proves container legitimacy; no session required for verify endpoint.
- **In-process async builds**: uses `asyncio.create_task` (not a separate worker). Production spec calls for RQ + Redis but this is not yet implemented.
- **Local Docker registry**: a `registry:2` sidecar in docker-compose serves built images on port 5050. After build, images are tagged and pushed to the registry, then cleaned from the local daemon. Users `docker pull` from the registry. The push target is `localhost:5050` (not the compose service name) because the Docker daemon runs on the host via socket mount.
- **Docker socket required**: builder needs `/var/run/docker.sock` mounted to build images and push to the registry.

### Database Models (api/models.py)

Four models: `User`, `UserImage` (build status: queued→building→ready→failed), `UserModule` (completion tracking per module), `Event` (global config with quota JSON).
