# CTF Training Platform — Full Specification

## Overview

A web-based CTF training platform where each user receives a uniquely generated Docker image with randomised vulnerabilities and hardening tasks baked in. Users run the container locally, fix the issues, then submit a verification command that checks their work. The platform handles image generation, distribution, verification, and scoring.

---

## Core Concept

1. User registers on the platform
2. Backend selects a random set of vulnerability and hardening modules for that user
3. A unique Docker image is built with those modules applied and a unique flag baked in
4. Image is pushed to ghcr.io with a UUID-based tag (public but obscure)
5. User receives a `docker pull` command and runs the container locally
6. User fixes misconfigurations / implements hardening tasks
7. User pastes a verification one-liner inside the container that POSTs system state to the backend
8. Backend validates state against the expected post-fix conditions and awards points

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend + Frontend | FastAPI + Jinja2 |
| Auth | bcrypt + session cookies (itsdangerous) |
| Database | SQLite (dev) / Postgres (prod) |
| Build worker | Python RQ + Redis |
| Image registry | ghcr.io (public images, UUID tags) |
| Reverse proxy | Caddy |
| Deployment | Docker Compose |

---

## Directory Structure

```
ctf-platform/
├── builder/
│   ├── main.py               # build orchestration entrypoint
│   ├── selector.py           # module selection logic
│   ├── renderer.py           # Dockerfile + manifest generation
│   └── registry.py           # ghcr.io push logic
├── modules/
│   ├── vulns/                # yaml + sh pairs (one per vulnerability)
│   └── hardening/            # yaml only (no scripts needed)
├── base/
│   └── Dockerfile            # base image, pre-built and pushed separately
├── templates/
│   └── Dockerfile.j2         # Jinja2 template for per-user image
├── api/
│   ├── main.py               # FastAPI app entrypoint
│   ├── routes/
│   │   ├── auth.py           # register, login, logout
│   │   ├── images.py         # trigger build, poll status, get pull command
│   │   ├── verify.py         # receive and validate verification submissions
│   │   └── scoreboard.py     # public scoreboard endpoint
│   ├── models.py             # SQLAlchemy DB models
│   ├── schemas.py            # Pydantic schemas
│   └── worker.py             # RQ worker entrypoint
├── frontend/
│   └── templates/
│       ├── base.html         # base layout
│       ├── landing.html      # login + register
│       ├── dashboard.html    # user dashboard
│       ├── scoreboard.html   # live scoreboard
│       └── admin.html        # admin panel
├── docker-compose.yml
├── Caddyfile
└── .env
```

---

## Module Schema

Every module (vulnerability or hardening task) is a YAML file. Vulnerability modules also have a paired shell script.

```yaml
# ─── COMMON FIELDS (all modules) ───────────────────────────────

id: string                    # unique snake_case identifier
name: string                  # human-readable name
description: string           # shown to the user on the platform
type: vulnerability | hardening
difficulty: easy | medium | hard
points: integer
category: string              # e.g. ssh, filesystem, services, authentication
tags: [string]                # optional, for filtering

conflicts: [id]               # modules that cannot be selected alongside this one
requires: [id]                # modules that must also be selected if this one is

# ─── VULNERABILITY SPECIFIC ────────────────────────────────────

script: string | null         # filename of shell script that introduces the misconfiguration
                              # null for hardening modules

# ─── VERIFICATION ───────────────────────────────────────────────

verification:
  type: string                # one of the verification types listed below
  # type-specific fields follow

hints: [string]               # progressively revealed hints (optional)
```

### Verification Types

```yaml
# File permissions
verification:
  type: file_permissions
  path: /etc/shadow
  expected: "640"

# File contains a value
verification:
  type: file_contains
  path: /etc/ssh/sshd_config
  pattern: "PermitRootLogin no"

# File does not contain a value
verification:
  type: file_not_contains
  path: /etc/ssh/sshd_config
  pattern: "PermitRootLogin yes"

# Service state
verification:
  type: service_running
  service: fail2ban
  expected: active            # active | inactive | enabled | disabled

# Package installed
verification:
  type: package_installed
  package: ufw

# Password not in a known default list
verification:
  type: password_not_default
  user: root
  wordlist: common_passwords  # references a bundled wordlist

# Port not listening
verification:
  type: port_closed
  port: 23

# Baked-in flag file (for CTF-style flag capture)
verification:
  type: flag_contents
  path: /root/flag.txt
```

### Example Vulnerability Module

```yaml
# modules/vulns/world_writable_shadow.yaml
id: world_writable_shadow
name: World-writable /etc/shadow
description: The /etc/shadow file has incorrect permissions, allowing any user to read or modify password hashes.
type: vulnerability
difficulty: easy
points: 100
category: filesystem
tags: [permissions, shadow, authentication]
conflicts: [loose_shadow_perms]
requires: []
script: world_writable_shadow.sh
verification:
  type: file_permissions
  path: /etc/shadow
  expected: "640"
hints:
  - "Check the permissions on sensitive authentication files"
  - "Use chmod and chown to correct /etc/shadow"
```

### Example Hardening Module

```yaml
# modules/hardening/install_fail2ban.yaml
id: install_fail2ban
name: Install and enable fail2ban
description: fail2ban is not installed. It should be installed and running to protect against brute-force attacks.
type: hardening
difficulty: medium
points: 200
category: services
tags: [brute-force, authentication, network]
conflicts: []
requires: []
script: null
verification:
  type: service_running
  service: fail2ban
  expected: active
hints:
  - "Look into intrusion prevention tools available via apt"
```

---

## Module Selection Logic (`selector.py`)

Selection respects tier quotas, conflict rules, and dependency requirements.

```python
def select_modules(quota: dict, module_library: list) -> list:
    """
    quota example:
    {
      "vulnerability": {"easy": 6, "medium": 1, "hard": 1},
      "hardening":     {"easy": 0, "medium": 1, "hard": 1}
    }
    """
    selected = []

    for module_type, tiers in quota.items():
        pool = [m for m in module_library if m.type == module_type]

        for difficulty, count in tiers.items():
            tier_pool = [m for m in pool if m.difficulty == difficulty]

            for _ in range(count):
                selected_ids = {m.id for m in selected}
                conflicts = {c for m in selected for c in m.conflicts}
                available = [
                    m for m in tier_pool
                    if m.id not in selected_ids
                    and m.id not in conflicts
                ]

                if not available:
                    raise ValueError(f"No available {difficulty} {module_type} modules")

                pick = random.choice(available)
                selected.append(pick)

                # resolve dependencies
                for req_id in pick.requires:
                    if req_id not in selected_ids:
                        req = find_module(req_id, module_library)
                        selected.append(req)

    return selected
```

---

## Image Build Pipeline (`builder/main.py`)

```
1. select_modules(quota, library)       → list of module objects
2. generate_flag(user_id)               → HMAC(secret, user_id) deterministic flag
3. generate_image_tag()                 → ghcr.io/<org>/ctf-<uuid4>:latest
4. render_dockerfile(modules)           → Dockerfile from Jinja2 template
5. prepare_build_context(modules)       → temp dir with Dockerfile + vuln scripts + manifest.json
6. docker build --build-arg FLAG=...   → builds image
7. docker push                          → pushes to ghcr.io
8. store_user_record(user_id, tag, modules, flag) → persists to DB
```

### Dockerfile Template (`templates/Dockerfile.j2`)

All vuln scripts are merged into a single RUN layer to keep the image lean.

```dockerfile
FROM ghcr.io/{{ registry_user }}/ctf-base:latest

{% if vuln_scripts %}
COPY scripts/ /tmp/scripts/
RUN chmod +x /tmp/scripts/*.sh \
  {% for script in vuln_scripts %}
  && /tmp/scripts/{{ script }} \
  {% endfor %}
  && rm -rf /tmp/scripts
{% endif %}

ARG FLAG
RUN echo "$FLAG" > /root/flag.txt && chmod 400 /root/flag.txt

COPY manifest.json /opt/ctf/manifest.json
```

### manifest.json

Baked into every image. Contains the user's assigned modules so the verification script knows what to check without calling home.

```json
{
  "user_id": "...",
  "modules": [
    {
      "id": "world_writable_shadow",
      "name": "World-writable /etc/shadow",
      "type": "vulnerability",
      "difficulty": "easy",
      "points": 100,
      "verification": {
        "type": "file_permissions",
        "path": "/etc/shadow",
        "expected": "640"
      }
    }
  ]
}
```

---

## Build Queue

Use **Python RQ + Redis** to handle concurrent build requests without contention.

- API enqueues a build job on registration → returns immediately
- Worker process picks up jobs and runs the build pipeline
- Dashboard polls `/api/images/status` until status is `ready`
- Recommended max concurrency: 2 simultaneous builds

---

## Verification System

### The one-liner (run inside the container)

```bash
curl -s https://ctf.yourdomain.com/api/verify \
  -H "Content-Type: application/json" \
  -d "$(python3 /opt/ctf/collect.py)"
```

`collect.py` is baked into the image. It reads `manifest.json`, collects the relevant system state for each module, and returns a JSON payload.

### Payload structure

```json
{
  "user_id": "...",
  "results": [
    {
      "module_id": "world_writable_shadow",
      "collected": {
        "permissions": "640",
        "owner": "root",
        "group": "shadow"
      }
    }
  ]
}
```

### Backend verification (`api/routes/verify.py`)

For each module result, the backend:
1. Looks up the module's verification spec
2. Compares collected state against expected state
3. Marks the module complete and awards points if passing
4. Returns per-module pass/fail so the user sees their progress

---

## Database Models (`api/models.py`)

```
User
  id, username, password_hash, created_at, is_admin

UserImage
  id, user_id (FK), image_tag, status (queued|building|ready|failed), created_at

UserModule
  id, user_id (FK), module_id, module_type, difficulty, points, completed, completed_at

Event
  id, name, quota (JSON), open, created_at
```

---

## API Routes

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account, enqueue build |
| POST | `/auth/login` | Login, set session |
| GET | `/auth/logout` | Clear session |
| GET | `/api/images/status` | Poll build status |
| GET | `/api/images/pull-command` | Get docker pull + run command |
| POST | `/api/verify` | Submit verification payload |
| GET | `/api/scoreboard` | Get scoreboard data |
| GET | `/admin/users` | List all users (admin) |
| POST | `/admin/rebuild/:user_id` | Trigger rebuild for a user (admin) |
| GET | `/admin/modules` | Browse module library (admin) |
| PUT | `/admin/event` | Update event config/quota (admin) |

---

## Frontend Pages

### Landing (`/`)
- Login and register forms
- On register: POST to `/auth/register` → redirect to dashboard with "building your environment..." state

### Dashboard (`/dashboard`)
- Build status indicator (queued → building → ready)
- When ready: copyable `docker pull` and `docker run` commands
- Module list: name, difficulty badge, description, points value, completion status
- Hints: revealed on request, with point penalty warning
- Total points tally

### Scoreboard (`/scoreboard`)
- Table: rank, username, total points, modules completed
- Auto-refreshes every 30 seconds

### Admin (`/admin`)
- Event settings: name, open/closed toggle, quota configuration
- User table: username, build status, points, rebuild button
- Module browser: all modules, filterable by type/difficulty/category

---

## Docker Compose

```yaml
services:
  api:
    build: .
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # builder needs docker access
      - .:/app
    env_file: .env
    depends_on: [redis, db]

  worker:
    build: .
    command: rq worker --url redis://redis:6379
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - .:/app
    env_file: .env
    depends_on: [redis]

  redis:
    image: redis:alpine

  db:
    image: postgres:16-alpine
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

---

## Environment Variables (`.env`)

```
SECRET_KEY=                  # used for session signing and flag HMAC
GHCR_TOKEN=                  # GitHub PAT with packages:write
GHCR_USER=                   # GitHub username or org
DATABASE_URL=                # postgres connection string
REDIS_URL=redis://redis:6379
EVENT_QUOTA={"vulnerability":{"easy":6,"medium":1,"hard":1},"hardening":{"easy":0,"medium":1,"hard":1}}
```

---

## Build Order for Claude Code

1. **`base/Dockerfile`** — minimal Ubuntu/Debian base with common tools pre-installed
2. **Sample modules** — one vuln (`world_writable_shadow`) and one hardening (`install_fail2ban`) to validate the schema and scripts
3. **`builder/`** — selector, renderer, registry, main orchestration
4. **`api/`** — models, auth routes, image routes, verify route, scoreboard route
5. **`frontend/templates/`** — landing, dashboard, scoreboard, admin
6. **`docker-compose.yml` + `Caddyfile`** — deployment configuration
7. **`collect.py`** — the in-container verification collector script
