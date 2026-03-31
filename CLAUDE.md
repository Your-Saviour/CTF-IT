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
docker-compose up -d

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

## Architecture

### Request Flow

User registers → async background task builds Docker image with selected modules → dashboard polls `/api/images/status` until ready → user pulls/runs container → fixes vulns → runs `collect.py` inside container → POSTs results to `/api/verify` → backend validates against expected state → awards points.

### Key Components

- **`api/`** — FastAPI app serving both HTML templates (Jinja2) and JSON API endpoints. Routes split into `auth`, `images`, `verify`, `scoreboard`, `admin`.
- **`builder/`** — Image build orchestration. `main.py` is the entry point: loads modules, selects per quota, renders Dockerfile from Jinja2 template, runs `docker build`, returns image tag + flag.
- **`modules/`** — Self-contained YAML definitions + optional shell scripts for vulnerabilities (`vulns/`) and hardening tasks (`hardening/`). Adding a new module = adding a YAML + optional .sh file, no code changes needed.
- **`templates/Dockerfile.j2`** — Jinja2 template for user container images. Copies vuln scripts, runs them, bakes in flag and manifest.
- **`base/`** — Base Docker image (Ubuntu 22.04 + common tools). All user images inherit from `ctf-base:latest`.
- **`collect.py`** — Runs inside user containers. Reads `/opt/ctf/manifest.json`, collects system state per module verification spec, outputs JSON for submission.
- **`frontend/templates/`** — Jinja2 HTML templates. Dark theme, client-side polling for build status.

### Module System

Each module is a YAML file with: `id`, `name`, `type` (vulnerability/hardening), `difficulty`, `points`, `category`, `script` (optional .sh), `verification` spec, `hints`, `conflicts`, `requires`.

Verification types: `file_permissions`, `file_contains`, `file_not_contains`, `service_running`, `package_installed`, `port_closed`, `flag_contents`, `password_not_default`, `password_changed`.

The selector (`builder/selector.py`) respects conflict exclusions and auto-resolves dependencies.

### Key Design Decisions

- **Deterministic flags**: `HMAC(secret_key, user_id)` — same user always gets same flag, enables rebuilds without storing flags separately.
- **Stateless verification**: flag in payload proves container legitimacy; no session required for verify endpoint.
- **In-process async builds**: uses `asyncio.create_task` (not a separate worker). Production spec calls for RQ + Redis but this is not yet implemented.
- **Docker socket required**: builder needs `/var/run/docker.sock` mounted to build images.

### Database Models (api/models.py)

Four models: `User`, `UserImage` (build status: queued→building→ready→failed), `UserModule` (completion tracking per module), `Event` (global config with quota JSON).
