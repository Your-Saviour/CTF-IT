# README & Admin Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite README.md as a single linear document covering all CTF-IT features, serving both first-time VPS setup and returning admin reference.

**Architecture:** Single README.md rewrite — 9 sections written in order, each section verified against source files before moving on. No new files. MODULE_GUIDE.md stays separate and is linked from the README.

**Spec:** `docs/superpowers/specs/2026-04-13-readme-and-admin-docs-design.md`

**Tech Stack:** Markdown only. Primary deployment reference: `deploy/docker-compose.yml`.

---

## Files

- Modify: `README.md` — full rewrite

---

### Task 1: Section 1 — Header + Overview

**Files:**
- Modify: `README.md` (replace entire file with Section 1 content to start fresh)

- [ ] **Step 1: Read current README.md and deploy/docker-compose.yml**

Read `README.md` (to know what to replace) and verify the service list in `deploy/docker-compose.yml` matches what you're about to write.

- [ ] **Step 2: Overwrite README.md with Section 1**

Replace the entire file with:

```markdown
# CTF-IT

A CTF training platform where each user gets a uniquely generated Docker container with randomised vulnerabilities and hardening tasks. Users fix issues inside their container and submit verification to the platform for scoring. Supports red team emulation via MITRE Caldera, automated VM provisioning via Vultr, and Ansible export for bare-metal deployments.

## Features

- **Docker image generation** — each participant gets a container with a randomised set of modules selected from the event quota
- **Multi-event support** — independent leaderboards, quotas, and settings per event
- **Blue team scoring** — vulnerabilities, hardening tasks, and payloads; points awarded when fixed
- **Red team emulation** — MITRE Caldera integration with attack trees, adversary operations, and goal objectives
- **VM auto-provisioning** — Vultr VPS creation and Ansible module deployment via Semaphore
- **Ansible export** — generate playbooks from any event quota for bare-metal deployments
- **Network topology** — live D3 force-directed graph of event → team → VM hierarchy
```

- [ ] **Step 3: Verify**

Confirm the six service names in `deploy/docker-compose.yml` are all represented in the feature list (CTF API, Caldera, Semaphore, Registry, Traefik, Dockhand map to the feature bullets above). No commit yet — more sections to come.

---

### Task 2: Section 2 — Quick Start

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read both env files**

Read `deploy/.env.example` and `.env.example` to confirm every variable mentioned below is accurate.

- [ ] **Step 2: Read deploy/caldera/config/local.yml.example**

Confirm the secrets list (`api_key_blue`, `api_key_red`, `encryption_key`, `crypt_salt`) and users block (`blue`/`red`/`admin`) match what you're about to document.

- [ ] **Step 3: Append Section 2 to README.md**

```markdown

## Quick Start

This guide covers deploying on a VPS with a domain. The full production stack lives in `deploy/` and uses Traefik for TLS termination and subdomain routing.

### Prerequisites

- VPS with Docker + Docker Compose installed
- Domain with DNS A record pointing to the server's public IP
- Firewall ports open:
  - `80`, `443` — Traefik (HTTP redirect + HTTPS)
  - `7010`–`7012`, `8022`, `2222`, `8853`, `8888` — Caldera agent communication (direct, not proxied)

### 1. Clone the repository

```bash
git clone <repo-url> ctf-it
cd ctf-it
```

### 2. Configure `deploy/.env`

```bash
cp deploy/.env.example deploy/.env
```

Edit `deploy/.env`:

| Variable | Description | How to generate |
|---|---|---|
| `DOMAIN` | Base domain, e.g. `example.com` | Your domain |
| `ACME_EMAIL` | Let's Encrypt notification email | Your email |
| `TRAEFIK_DASHBOARD_AUTH` | Basic auth for Traefik dashboard | `echo "$(htpasswd -nB admin)" \| sed 's/\$/\$\$/g'` |
| `REGISTRY_AUTH` | Basic auth for Docker registry | Same as above |
| `SEMAPHORE_ADMIN_PASSWORD` | Semaphore admin password | `openssl rand -base64 32` |
| `SEMAPHORE_POSTGRES_PASSWORD` | Semaphore database password | `openssl rand -base64 32` |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | Semaphore secrets encryption key | `openssl rand -base64 32` |
| `CALDERA_AGENT_URL` | Public address agents beacon to | `http://<SERVER_IP>:8888` — use the server's **public IP**, not the domain. Port 8888 is published directly, not reverse-proxied. |

Optional VM provisioning variables:

```bash
VULTR_API_KEY=your-key            # Enables Vultr VM creation from admin panel
VULTR_DEFAULT_REGION=ewr          # Default Vultr region (ewr, lax, syd, mel, ams, etc.)
CLOUDFLARE_API_TOKEN=your-token   # Auto-creates DNS A records on VM creation
CLOUDFLARE_DOMAIN=example.com
```

### 3. Configure root `.env`

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Description | How to generate |
|---|---|---|
| `SECRET_KEY` | Session signing + deterministic flag generation | `openssl rand -base64 32` |
| `EVENT_QUOTA` | Default module quota for the first event | See [Events & Scoring](#events--scoring) |
| `ROOT_PASSWORD` | Default root password baked into base images | Choose a value; `password_changed` modules check against this |

### 4. Configure Caldera

```bash
cp deploy/caldera/config/local.yml.example deploy/caldera/config/local.yml
```

Edit `deploy/caldera/config/local.yml` and replace **every** `REPLACE_ME` value:

- `api_key_blue`, `api_key_red`, `encryption_key`, `crypt_salt` — generate each with `openssl rand -base64 32`
- `users.blue.blue`, `users.red.admin`, `users.red.red` — set passwords
- `app.contact.tunnel.ssh.user_password`, `app.contact.ftp.pword` — set passwords

### 5. Build the base image

```bash
docker build --build-arg "$(cat base/.env)" -t ctf-base:latest base/
```

**Cross-architecture builds** (e.g. AMD64 server, Apple Silicon users):

```bash
# One-time QEMU registration
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# Build for target architecture
docker build --platform linux/arm64 --build-arg "$(cat base/.env)" -t ctf-base:latest base/
```

Also set `DOCKER_PLATFORM=linux/arm64` in root `.env`.

### 6. Start the stack

```bash
cd deploy
docker compose up -d
```

Services become available at (replace `example.com` with your `DOMAIN`):

| Service | URL | Description |
|---|---|---|
| CTF dashboard | `https://ctf.example.com` | User registration, dashboard, scoreboard |
| MITRE Caldera | `https://caldera.example.com` | Red team C2 server |
| Ansible Semaphore | `https://semaphore.example.com` | Playbook execution UI |
| Docker registry | `https://registry.example.com` | Built image distribution |
| Dockhand | `https://dockhand.example.com` | Container management UI |
| Traefik dashboard | `https://traefik.example.com` | Reverse proxy dashboard |

### 7. Create the admin account

Navigate to `https://ctf.example.com` and register. **The first user to register automatically becomes admin.** Register before sharing the URL with participants.

### 8. Configure Docker registry login (user machines)

The registry is behind TLS and basic auth. Every machine that pulls images (including the server itself during development) must log in:

```bash
docker login registry.example.com
# Username: admin (or whatever you set in REGISTRY_AUTH)
# Password: the password you set
```

No `insecure-registries` config needed — Traefik provides valid TLS certificates via Let's Encrypt.
```

- [ ] **Step 4: Verify**

Check: `deploy/.env.example` contains `CALDERA_AGENT_URL`, `TRAEFIK_DASHBOARD_AUTH`, `REGISTRY_AUTH`, `SEMAPHORE_ACCESS_KEY_ENCRYPTION`. Check `deploy/caldera/config/local.yml.example` contains the secrets listed. Check all service subdomain names match labels in `deploy/docker-compose.yml`.

---

### Task 3: Section 3 — Events & Scoring

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read api/models.py lines covering Event and VMGoal**

Confirm `Event.status` values (`draft`, `open`, `stopped`) and `VMGoal` fields (`achievement_count`, `defend_count`, `red_points`, `defend_points`) match what you're about to write.

- [ ] **Step 2: Append Section 3 to README.md**

```markdown

## Events & Scoring

Events are the central organising unit. Each event has its own module quota, leaderboard, and settings. Users register into exactly one event.

### Event lifecycle

| Status | Meaning |
|---|---|
| `draft` | Invisible to users; safe to configure |
| `open` | Accepts user registration |
| `stopped` | Archived; leaderboard frozen; verification blocked |

Manage events from the admin panel: create → configure → start → stop.

### Creating an event

Required fields: name, quota JSON.

Optional: description (shown on registration page), welcome message (shown on user dashboard after registration), time limit in minutes (display only — enforcement is manual via stop).

### Quota JSON

The quota defines how many modules of each type and difficulty are selected per user at registration time.

```json
{
  "vulnerability": {"easy": 1, "medium": 2, "hard": 1},
  "hardening":     {"easy": 1, "medium": 1, "hard": 0},
  "payload":       {"easy": 1, "medium": 0, "hard": 0},
  "application_external": {"easy": 1},
  "goal":          {"easy": 1},
  "categories":    {"authentication": 2},
  "tags":          {"privilege-escalation": 1}
}
```

Valid type keys: `vulnerability`, `hardening`, `payload`, `application_external`, `application_internal`, `goal`.

`categories` and `tags` are inclusive filters — modules already selected by type/difficulty count toward these totals. Use them to guarantee coverage of a particular topic without double-selecting.

### Scoring model

| Score type | Awarded when | Formula |
|---|---|---|
| Blue defensive | User completes a `preapplied` module | `points` per module |
| Blue reactive | Blue team reverts a red team goal | `defend_points × defend_count` per goal |
| Red offensive | Red team achieves a goal | `red_points × achievement_count` per goal |

### Scoreboards

- `GET /api/scoreboard?event_id=N` — blue team per-event rankings (public)
- `GET /admin/caldera/scoreboard?event_id=N` — red vs blue combined view (admin only)
- `GET /api/scoreboard/events` — lists all non-draft events for the selector dropdown

Leaderboards are frozen when event status transitions to `stopped`.
```

- [ ] **Step 3: Verify**

Check `api/routes/scoreboard.py` contains `/api/scoreboard` and `/api/scoreboard/events`. Check `api/routes/caldera_export.py` or similar contains `/admin/caldera/scoreboard`. Confirm quota key names match `builder/selector.py` logic.

---

### Task 4: Section 4 — Modules

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Check modules directory structure**

Run `ls modules/` to confirm: `vulns/`, `hardening/`, `payloads/`, `application_external/`, `application_internal/`, `goals/` all exist.

- [ ] **Step 2: Append Section 4 to README.md**

```markdown

## Modules

Modules are self-contained YAML definitions with optional shell scripts. See [MODULE_GUIDE.md](MODULE_GUIDE.md) for the full reference: YAML fields, verification types, build steps, and examples.

### Module types

| Type | Description | Points |
|---|---|---|
| `vulnerability` | Misconfiguration introduced at build time; user fixes it | `points` on fix |
| `hardening` | Security measure the user must implement from scratch | `points` on completion |
| `payload` | Malicious artifact planted at build time; user finds and removes it | `points` on removal |
| `application_external` | Network-accessible service (web app, API) targeted by other modules | 0 |
| `application_internal` | System-level tool (Docker daemon, VS Code Server) targeted by other modules | 0 |
| `goal` | Red team objective — drives red vs blue scoring | see below |

### `stage` field (vulnerability and payload modules only)

| Stage | Effect |
|---|---|
| `preapplied` (default) | Visible on blue team dashboard; scored when fixed |
| `caldera` | Hidden from blue team dashboard, hints, and scoring; Caldera discovers and exploits it |

### Goal modules

Goal modules represent red team objectives: deface a website, install a C2 beacon, exfiltrate `/etc/shadow`. They live in `modules/goals/` and appear as terminal nodes (phase 8) in the attack tree.

**Lifecycle (cyclical):** `pending → achieved → defended → achieved → ...`

Each cycle increments `achievement_count` (red team points) or `defend_count` (blue team points). Blue team is incentivised to fix root causes — Caldera can re-exploit on the next check cycle.

Key YAML fields beyond standard module fields:

| Field | Description |
|---|---|
| `red_points` | Awarded to red team each time the goal is achieved |
| `defend_points` | Awarded to blue team each time the goal is reverted |
| `verification` | Detects goal was achieved (e.g. `http_response` body_contains) |
| `revert_verification` | Detects blue team reverted it |

→ See [MODULE_GUIDE.md](MODULE_GUIDE.md) for full YAML reference, all verification types, build steps, and complete examples.
```

- [ ] **Step 3: Verify**

Confirm `modules/goals/` directory exists. Confirm `stage` field is referenced in `CLAUDE.md` or `api/models.py` `VMModule` model.

---

### Task 5: Section 5 — Red Team (Caldera)

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read builder/caldera.py and builder/attack_tree.py top-of-file comments**

Confirm kill chain phase order and the two generation modes (`generate_caldera_export` vs `generate_caldera_export_multi_path`) are as described below.

- [ ] **Step 2: Read api/routes/caldera_ops.py and caldera_setup.py**

Confirm endpoint paths for operations CRUD, vm-summary, scoreboard, and caldera-setup.

- [ ] **Step 3: Append Section 5 to README.md**

```markdown

## Red Team (Caldera)

The Caldera integration generates an adversary emulation plugin from selected modules, loads it into MITRE Caldera, and runs attack operations against blue team VMs. It is a core feature, not an add-on.

### How it works

1. Modules with `caldera` metadata define reconnaissance and exploitation steps
2. CTF-IT generates a Caldera plugin (abilities + adversary definitions) from these modules
3. The plugin is installed into the running Caldera container
4. Caldera runs adversary operations against VMs; results feed back into the attack tree
5. Goal modules act as terminal objectives — achieving or reverting them awards red/blue points

### Setup

Before using Caldera features:

1. Ensure `deploy/caldera/config/local.yml` has all `REPLACE_ME` values replaced (see Quick Start)
2. Set `CALDERA_AGENT_URL=http://<SERVER_IP>:8888` in `deploy/.env` — this is the address agents on target VMs use to beacon back. Use the **public IP** (not domain), because port 8888 is published directly to the host, not reverse-proxied through Traefik
3. Ensure firewall allows inbound on: `7010`, `7011/udp`, `7012`, `8022`, `2222`, `8853`, `8888`

### Plugin export

Download the Caldera plugin zip for inspection or manual upload:

```bash
curl -X POST https://ctf.example.com/admin/caldera-export \
  -H "Cookie: session=<admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"event_id": 1}' \
  -o ctf-exploit.zip
```

Or use `{"quota": {...}}` instead of `{"event_id": N}` to export from an arbitrary quota.

### One-click setup

The recommended path — does everything automatically:

**Admin panel → Caldera → Setup** (or `POST /admin/caldera-setup` with `{"event_id": N}`)

This:
1. Generates the plugin from the event's modules
2. Installs it to `deploy/caldera/plugins/ctf-exploit/`
3. Patches `deploy/caldera/config/local.yml` to enable the `ctf-exploit` plugin
4. Restarts the Caldera container and waits for it to become healthy
5. Creates a "CTF Red Team Emulation" operation
6. Caches attack tree JSON on each VM in the event

### Attack trees

Each VM gets a directed acyclic graph (DAG) of its assigned modules ordered by ATT&CK kill chain phase:

| Phase | Name |
|---|---|
| -1 | infrastructure |
| 0 | initial-access |
| 1 | execution |
| 2 | persistence |
| 3 | privilege-escalation |
| 4 | credential-access |
| 5 | collection |
| 6 | impact |
| 7 | command-and-control |
| 8 | goal (terminal nodes) |

Node colours in the visualisation: green = exploited, red = defended, gray = skipped, cyan = running.

Attack trees are rendered on the VM detail page and the Caldera operation detail page.

**API:**
- `GET /admin/caldera/attack-tree/{vm_id}` — raw tree JSON
- `GET /admin/caldera/operations/{op_id}?include_tree=true` — tree annotated with operation results

### Operations

```bash
# List operations
GET /admin/caldera/operations

# Create operation
POST /admin/caldera/operations
{"event_id": 1}

# Delete operation
DELETE /admin/caldera/operations/{op_id}

# Per-VM attack aggregates
GET /admin/caldera/vm-summary

# VM-specific results
GET /admin/caldera/vm/{vm_id}/results
```

### Goal state machine

Run a check cycle against a VM's goal:

```bash
POST /admin/vms/{vm_id}/goals/{goal_id}/check
```

CTF-IT runs `verification` (did red team achieve the goal?) and `revert_verification` (did blue team revert it?) against the VM, transitions the state, and awards points accordingly.

Currently supports `http_response` verification type for remote VMs. Other verification types return `501 Not Implemented`.

### Red vs blue scoreboard

```bash
GET /admin/caldera/scoreboard?event_id=N
```

Returns per-team `blue_defensive` (completed preapplied module points), `blue_reactive` (goal reverts × defend_points), and `red_offensive` (goal achievements × red_points).
```

- [ ] **Step 4: Verify**

Check `api/routes/caldera_setup.py` for the `/admin/caldera-setup` endpoint. Check `api/routes/caldera_ops.py` for `/admin/caldera/operations`, `/admin/caldera/vm-summary`, `/admin/caldera/vm/{vm_id}/results`. Check `api/routes/caldera_tree.py` for `/admin/caldera/attack-tree/{vm_id}`.

---

### Task 6: Section 6 — VM Provisioning

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read deploy/docker-compose.yml semaphore service**

Confirm `ctf-shared_playbooks` volume is mounted to both `api` and `semaphore` services — this is the shared volume for staged playbooks.

- [ ] **Step 2: Read builder/vm_quota_validation.py**

Confirm valid role values (`target`, `attacker`) and required fields (`os`, `default_plan`, `count`, `role`) for vm_quota JSON.

- [ ] **Step 3: Append Section 6 to README.md**

```markdown

## VM Provisioning

CTF-IT can provision and manage VMs for team-based events. VMs are team-scoped. Modules are assigned to VMs the same way they are to Docker containers.

### Teams

Create teams per event from the admin panel (Admin → Teams & VMs). Teams cannot be deleted while they have VMs.

### Registering an existing VM

Use **Register Existing** in the admin panel to add a machine already provisioned elsewhere (bare metal, another cloud, etc.). Only IP address and SSH connection details are required — no Vultr API key needed.

### Vultr provisioning

Requires `VULTR_API_KEY` and `VULTR_DEFAULT_REGION` in root `.env`.

1. Admin panel → Teams & VMs → **Create VM** → **Create on Vultr** tab
2. Choose team, OS, and plan (dropdowns populated live from the Vultr API)
3. Submit — CTF-IT stages `playbooks/create-vm.yml`, runs it via Semaphore, and polls for result
4. VM is registered with its public IP; if `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_DOMAIN` are set, a DNS A record (`hostname.DOMAIN`) is created automatically

To destroy: VM detail page → **Destroy on Vultr** — runs `playbooks/destroy-vm.yml`, removes the Vultr instance, deletes the DNS record, and removes the VM from the database.

### VM quota (auto-provisioning on event start)

Define a `vm_quota` JSON on an event to automatically provision all VMs when the event starts.

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

| Role | Behaviour |
|---|---|
| `target` | Modules assigned, Ansible playbook deployed after VM creation. Plan auto-sized from module `min_ram_mb`/`min_vcpu` requirements. |
| `attacker` | Bare OS only, no modules. Goes directly to `active` after Vultr creation. |

`POST /admin/events/{id}/start` triggers auto-provisioning when `vm_quota` is present.

**Provisioning status:**

`GET /admin/events/{id}/provision-status` — returns aggregate progress (`total`, `creating`, `registered`, `provisioning`, `active`, `failed`) and a per-VM status list. The admin UI polls this every 5 seconds and shows a real-time progress bar and "Retry Failed" button.

### Network topology

`/admin/topology` — interactive D3 force-directed graph showing the event → team → VM hierarchy.

- Hover for IP, OS, and module progress bar
- Right-click for context menu (view details, provision, assign modules, export playbook, destroy)
- Double-click to navigate to detail page
- Live polling every 5 seconds — new nodes fade in, removed nodes fade out
- VM node border colour reflects status: green (active), amber (creating/provisioning), red (failed), grey (registered/stopped)
- Filter by event with the dropdown in the toolbar
```

- [ ] **Step 4: Verify**

Check `playbooks/create-vm.yml` exists. Check `api/routes/vm.py` for `/admin/events/{id}/provision-status`. Check `VULTR_API_KEY` is documented in `.env.example`.

---

### Task 7: Section 7 — Ansible Export

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read builder/ansible.py**

Confirm export endpoint accepts both `quota` and `event_id` inputs, and that output includes `playbook.yml`, `scripts/`, and `files/`.

- [ ] **Step 2: Append Section 7 to README.md**

```markdown

## Ansible Export

As an alternative to Docker containers, administrators can export modules as Ansible playbooks to apply the same vulnerabilities, hardening tasks, and applications to bare machines or VMs.

### Export

```bash
# Export from an event's quota
curl -X POST https://ctf.example.com/admin/ansible-export \
  -H "Cookie: session=<admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"event_id": 1}' \
  -o ctf-playbook.zip

# Export from an explicit quota
curl -X POST https://ctf.example.com/admin/ansible-export \
  -H "Cookie: session=<admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"quota": {"vulnerability": {"easy": 2, "medium": 1, "hard": 0}}}' \
  -o ctf-playbook.zip
```

### Running the playbook

```bash
unzip ctf-playbook.zip -d ctf-playbook
cd ctf-playbook
ansible-playbook -i inventory playbook.yml --become
```

Target machines should be Ubuntu 22.04 with the same base packages as the `ctf-base` Docker image.

### Output structure

```
ctf-playbook/
  playbook.yml
  scripts/
    suid_find__suid_find.sh
    inventory_dashboard__setup.sh
  files/
    inventory_dashboard__app.py
    inventory_dashboard__inventory.service
```

### Module compatibility

All modules are automatically compatible with Ansible export. The playbook uses:
- `ansible.builtin.script` for `run` steps (same `.sh` scripts as Docker builds)
- `ansible.builtin.copy` for `copy` steps (same files, same destination paths)

**Note for module authors:** Docker builds create parent directories automatically when copying files; Ansible on bare machines does not. Use `mkdir -p` in `run` steps before copying files to new directories.
```

- [ ] **Step 3: Verify**

Check `builder/ansible.py` for `generate_ansible_export` function and that it accepts both `quota` and `event_id`. Check `templates/playbook.yml.j2` exists.

---

### Task 8: Section 8 — Project Structure

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Run ls on api/routes/ and builder/ to confirm file list**

```bash
ls api/routes/
ls builder/
ls modules/
```

- [ ] **Step 2: Append Section 8 to README.md**

```markdown

## Project Structure

```
CTF-IT/
  api/                          # FastAPI application
    routes/                     # Route handlers
      auth.py                   # Registration, login, logout
      images.py                 # Image build status polling
      verify.py                 # Submission and scoring
      scoreboard.py             # Per-event blue team scoreboard
      admin.py                  # Admin panel and event management
      ansible_export.py         # Ansible playbook export
      caldera_export.py         # Caldera plugin export
      caldera_setup.py          # One-click Caldera setup
      caldera_ops.py            # Caldera operations and results API
      caldera_tree.py           # Attack tree API
      vm.py                     # Team/VM CRUD and topology
      vm_goals.py               # VMGoal state machine API
    services/
      semaphore.py              # Ansible Semaphore REST client
    models.py                   # Database models
    main.py                     # App entry point
  base/                         # Base Docker image (Ubuntu 22.04 + systemd)
  builder/                      # Image build orchestration
    main.py                     # Build entry point
    selector.py                 # Module selection (quota, conflicts, deps)
    renderer.py                 # Dockerfile + manifest generation
    ansible.py                  # Ansible playbook export
    caldera.py                  # Caldera plugin generation
    attack_tree.py              # Attack tree DAG construction and DFS path extraction
    registry.py                 # Image tagging and registry push
    module_loader.py            # YAML module parsing
    plan_sizing.py              # Vultr plan sizing from module resource requirements
    vm_quota_validation.py      # vm_quota JSON schema validation
    quota_validation.py         # EVENT_QUOTA JSON schema validation
  modules/                      # Module definitions
    vulns/                      # Vulnerability modules
    hardening/                  # Hardening modules
    payloads/                   # Payload modules
    application_external/       # External application modules
    application_internal/       # Internal application modules
    goals/                      # Goal modules (red team objectives)
  templates/
    Dockerfile.j2               # Jinja2 template for user images
    playbook.yml.j2             # Jinja2 template for Ansible playbook export
  frontend/
    templates/                  # Jinja2 HTML templates
  playbooks/                    # Ansible playbooks for Vultr VM lifecycle
    create-vm.yml               # Provision Vultr VPS + optional Cloudflare DNS record
    destroy-vm.yml              # Destroy Vultr instance + remove DNS record
    collections/
      requirements.yml          # vultr.cloud + community.general (auto-installed by Semaphore)
  deploy/                       # Production deployment stack
    docker-compose.yml          # Traefik + Dockhand + API + Registry + Caldera + Semaphore
    .env.example                # Deployment environment variables
    caldera/
      config/
        local.yml.example       # Caldera configuration template
      plugins/
        ctf-exploit/            # Caldera plugin directory (populated by caldera-setup)
    traefik/
      traefik.yml               # Traefik static configuration
  audit.py                      # In-container audit script (broad system snapshot)
  docker-compose.yml            # Development/testing stack only (no Traefik, no TLS)
  requirements.txt              # Python dependencies
  MODULE_GUIDE.md               # Module authoring reference
  TEST_PLAN.md                  # End-to-end integration test plan
```
```

- [ ] **Step 3: Verify**

Cross-check the route file list against `ls api/routes/`. Cross-check the builder file list against `ls builder/`. Confirm `modules/goals/` exists.

---

### Task 9: Section 9 — Testing

**Files:**
- Modify: `README.md` (append section)

- [ ] **Step 1: Read TEST_PLAN.md first two sections**

Confirm the e2e test command and `.env.test` description are accurate.

- [ ] **Step 2: Append Section 9 to README.md**

```markdown

## Testing

> **Note:** Tests run against the root `docker-compose.yml` (the development stack, no Traefik). **Do not** run tests against `deploy/docker-compose.yml`.

See [TEST_PLAN.md](TEST_PLAN.md) for the full end-to-end integration test.

To run the automated e2e test:

```bash
docker compose down -v && tests/e2e_test.sh
```

`.env.test` is checked into the repo with a quota that selects all 10 modules (9 scored + 1 app). The e2e script copies it to `.env` automatically.

`ROOT_PASSWORD` in `base/.env` is `changeme123` — this is the known default the `password_changed` verification module checks against.
```

- [ ] **Step 3: Verify**

Confirm `tests/e2e_test.sh` exists. Confirm `.env.test` is in the repo root.

---

### Task 10: Remove old Key Design Decisions section and final commit

**Files:**
- Modify: `README.md` (remove old section, clean up)

- [ ] **Step 1: Read the current README.md**

Find the old `## Key Design Decisions` section (near the bottom) and the old `## Registry` section. These are now superseded by the new content.

- [ ] **Step 2: Remove superseded sections**

Delete `## Key Design Decisions` and `## Registry` sections from the README. Both are either covered in the new sections or in CLAUDE.md and don't belong in user-facing docs.

- [ ] **Step 3: Final read-through**

Read the entire README.md from top to bottom. Check:
- No broken section references (e.g. anchors like `#events--scoring` match actual headers)
- No orphaned old content mixed in
- Section order matches: Overview → Quick Start → Events → Modules → Red Team → VM Provisioning → Ansible Export → Project Structure → Testing

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README with full feature coverage and VPS admin guide"
```

---

## Self-Review Notes

- All 9 spec sections have corresponding tasks ✓
- `deploy/.env.example` variables all present in Task 2 table ✓
- `caldera/config/local.yml.example` secrets all named in Task 2 ✓
- Kill chain phases in Task 5 match CLAUDE.md `infrastructure(-1) → ... → goal(8)` ✓
- vm_quota `role` values (`target`, `attacker`) match `builder/vm_quota_validation.py` ✓
- Module types table in Task 4 matches all six dirs under `modules/` ✓
- Scoreboard endpoints in Task 3 reference correct routes ✓
- No TBD, TODO, or placeholder content ✓
