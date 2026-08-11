# CTF-IT

A VM-based red-team/blue-team training platform. Administrators create events and teams, provision Vultr VMs, apply randomised vulnerability and hardening modules through Ansible Semaphore, and run adversary operations through MITRE Caldera.

## Features

- **Team VM environments** — each event provisions role-based target, attacker, and firewall VMs from an event quota
- **Multi-event support** — independent leaderboards, quotas, and settings per event
- **Blue team scoring** — vulnerabilities, hardening tasks, and payloads; points awarded when fixed
- **Red team emulation** — MITRE Caldera integration with attack trees, adversary operations, and goal objectives
- **AI Agent** — autonomous red team agent with real-time updates, health monitoring, and error feedback
- **VM auto-provisioning** — Vultr VPS creation and Ansible module deployment via Semaphore
- **Ansible export** — generate playbooks from any event quota for bare-metal deployments
- **Network topology** — live D3 force-directed graph of event → team → VM hierarchy

## Quick Start

This guide covers deploying on a Linux VPS with a domain. The production stack lives in `deploy/` and uses Traefik for TLS termination and subdomain routing.

### Automated (recommended)

On a fresh Linux server, deploy the full stack with one command:

```bash
git clone <repo-url> CTF-IT && cd CTF-IT
./quickstart.sh
```

The script installs Docker if needed, collects deployment settings, creates the
root `.env`, `deploy/.env`, and Caldera configuration with restricted file
permissions, then starts `deploy/docker-compose.yml`. Re-running preserves
existing configuration. Use `--force` to back up and regenerate configuration,
or `--non-interactive` to read inputs from environment variables.

After it finishes, create DNS A-records for `ctf`, `caldera`, `semaphore`,
`dockhand`, and `traefik` under your domain, pointing at the server. The manual
steps below document what the script automates, and remain available if you
prefer to configure the stack by hand.

### Prerequisites

- VPS with Docker + Docker Compose installed
- A domain and permission to create DNS A records
- Enough free disk and time for the first deployment to build Apache Caldera
  from its official release source (subsequent starts reuse the local image)
- Firewall ports open:
  - `80`, `443` — Traefik (HTTP redirect + HTTPS)
  - `7010`–`7012`, `8022`, `2222`, `8853`, `8888` — Caldera agent communication (direct, not proxied)

### Manual deployment

If you do not use `quickstart.sh`, create the three required configuration files:

```bash
cp deploy/.env.example deploy/.env
cp .env.example .env
cp deploy/caldera/config/local.yml.example deploy/caldera/config/local.yml
```

Replace all example credentials and `REPLACE_ME` values. Caldera also requires
an encrypted SSH tunnel host key. Generate it, mount it at the path already
declared by the production Compose file, and put the same passphrase in
`app.contact.tunnel.ssh.host_key_passphrase`:

```bash
openssl genpkey -algorithm RSA -aes-256-cbc \
  -pass pass:<KEY_PASSPHRASE> -pkeyopt rsa_keygen_bits:3072 \
  -out deploy/caldera/config/ssh_host_key
```

Set `app.contact.tunnel.ssh.host_key_file` to
`/usr/src/app/conf/ssh_host_key`. The important deployment variables are:

| Variable | Description | How to generate |
|---|---|---|
| `DOMAIN` | Base domain, e.g. `example.com` | Your domain |
| `ACME_EMAIL` | Let's Encrypt notification email | Your email |
| `TRAEFIK_DASHBOARD_AUTH` | Basic auth for Traefik dashboard | `echo "$(htpasswd -nB admin)" \| sed 's/\$/\$\$/g'` |
| `SEMAPHORE_ADMIN_PASSWORD` | Semaphore admin password | `openssl rand -base64 32` |
| `SEMAPHORE_POSTGRES_PASSWORD` | Semaphore database password | `openssl rand -base64 32` |
| `CTF_POSTGRES_PASSWORD` | CTF API PostgreSQL password (URL-safe) | `openssl rand -hex 32` |
| `SEMAPHORE_ACCESS_KEY_ENCRYPTION` | Semaphore secrets encryption key | `openssl rand -base64 32` |
| `CALDERA_AGENT_URL` | Public address agents beacon to | `http://<SERVER_IP>:8888` — use the server's **public IP**, not the domain. Port 8888 is published directly, not reverse-proxied. |

Optional VM provisioning variables:

```bash
VULTR_API_KEY=your-key            # Enables Vultr VM creation from admin panel
VULTR_DEFAULT_REGION=ewr          # Default Vultr region (ewr, lax, syd, mel, ams, etc.)
CLOUDFLARE_API_TOKEN=your-token   # Auto-creates DNS A records on VM creation
CLOUDFLARE_DOMAIN=example.com
```

The root `.env` configures API secrets and integrations. The production stack
uses its dedicated PostgreSQL service; root `DATABASE_URL` is only used by the
local development Compose stack.

| Variable | Description | How to generate |
|---|---|---|
| `SECRET_KEY` | Session signing + deterministic flag generation | `openssl rand -base64 32` |
| `DATA_ENCRYPTION_KEY` | Encrypts stored infrastructure credentials | `openssl rand -base64 32` |
| `EVENT_QUOTA` | Default module quota for the first event | See [Events & Scoring](#events--scoring) |
Set all generated files to owner-only access, then start the stack:

```bash
chmod 600 .env deploy/.env deploy/caldera/config/local.yml deploy/caldera/config/ssh_host_key
docker compose --file deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Services become available at (replace `example.com` with your `DOMAIN`):

| Service | URL | Description |
|---|---|---|
| CTF dashboard | `https://ctf.example.com` | Invite-based onboarding, dashboard, scoreboard |
| MITRE Caldera | `https://caldera.example.com` | Red team C2 server |
| Ansible Semaphore | `https://semaphore.example.com` | Playbook execution UI |
| Dockhand | `https://dockhand.example.com` | Container management UI |
| Traefik dashboard | `https://traefik.example.com` | Reverse proxy dashboard |

### Create the admin account

Navigate to `https://ctf.example.com` and register using the generated `ADMIN_BOOTSTRAP_TOKEN`. The token is required for the first account, which becomes the administrator. After bootstrap, public registration is disabled: create event-bound invitation links from the Users panel and send them through your normal organizer communication channel.

The Users panel is also where administrators assign events, promote or demote accounts, deactivate or reactivate access, and create one-hour password-reset links. Invitation links expire after seven days. Both link types are single-use, and access changes invalidate the affected user's active sessions.

## Events & Scoring

Events are the central organising unit. Each event has its own module quota, leaderboard, and settings. Invitation links assign new participants to an event; administrators can reassign them later.

### Event lifecycle

| Status | Meaning |
|---|---|
| `draft` | Invisible to users; safe to configure |
| `open` | Active for participants and scoring |
| `stopped` | Archived; leaderboard frozen; verification blocked |

Manage events from the admin panel: create → configure → start → stop.

### Creating an event

Required fields: name, quota JSON.

Optional: description, welcome message, and a time limit in minutes. Starting a timed event records its deadline; the application automatically transitions it to `stopped` when the deadline passes.

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
- `GET /admin/api/caldera/scoreboard?event_id=N` — red vs blue combined view (admin only)
- `GET /api/scoreboard/events` — lists all non-draft events for the selector dropdown

Leaderboards are frozen when event status transitions to `stopped`.

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
curl -X POST https://ctf.example.com/admin/api/ \
  -H "Cookie: session=<admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"event_id": 1}' \
  -o ctf-exploit.zip
```

Or use `{"quota": {...}}` instead of `{"event_id": N}` to export from an arbitrary quota.

### One-click setup

The recommended path — does everything automatically:

**Admin panel → Caldera → Setup** (or `POST /admin/api/` with `{"event_id": N}`)

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
- `GET /admin/api/caldera/attack-tree/{vm_id}` — raw tree JSON
- `GET /admin/api/caldera/operations/{op_id}?include_tree=true` — tree annotated with operation results

### Operations

```
GET    /admin/api/caldera/operations            # List operations
POST   /admin/api/caldera/operations            # Create operation {"event_id": N}
DELETE /admin/api/caldera/operations/{op_id}    # Delete operation
GET    /admin/api/caldera/vm-summary            # Per-VM attack aggregates
GET    /admin/api/caldera/vm/{vm_id}/results    # VM-specific operation results
```

### Goal state machine

Run a check cycle against a VM's goal:

```
POST /admin/api/vms/{vm_id}/goals/{goal_id}/check
```

CTF-IT runs `verification` (did red team achieve the goal?) and `revert_verification` (did blue team revert it?) against the VM, transitions the state, and awards points accordingly.

Remote goal checks support HTTP responses, systemd service state, and file existence/absence. SSH-based checks use the platform key and pin the VM's first observed host key, rejecting later changes.

### Red vs blue scoreboard

```
GET /admin/api/caldera/scoreboard?event_id=N
```

Returns per-team `blue_defensive` (completed preapplied module points), `blue_reactive` (goal reverts × defend_points), and `red_offensive` (goal achievements × red_points).

### AI Agent

The AI Agent provides autonomous penetration testing capabilities with human-in-the-loop approval. Key features:

**Real-time Updates:**
- WebSocket connections for instant status updates
- Auto-refresh on action completion
- Health monitoring with real-time score tracking
- Error tracking with severity levels

**Operation Management:**
- EGATS planner with UCB node selection and TDI difficulty scoring
- Task Difficulty Assessment (4 weighted dimensions)
- Promise backpropagation for adaptive planning
- Multi-target attack surface prioritization

**Session Control:**
- Create sessions targeting events, VMs, or IP addresses
- Manual or auto-stepping modes
- Approval workflow for human-in-the-loop execution
- Session resume with tree integrity validation

**Error Handling:**
- Comprehensive error tracking and history
- Operation health scoring (0-1 scale)
- Retry suggestions for failed operations
- Error categorization and context tracking

**API Endpoints:**
```
# Session management
POST   /admin/api/ai-agent/sessions             # Create new session
GET    /admin/api/ai-agent/sessions             # List sessions
GET    /admin/api/ai-agent/sessions/{id}        # Session details
POST   /admin/api/ai-agent/sessions/{id}/start  # Start session
POST   /admin/api/ai-agent/sessions/{id}/stop   # Stop session
POST   /admin/api/ai-agent/sessions/{id}/step   # Plan next action
POST   /admin/api/ai-agent/sessions/{id}/approve/{action_id}  # Approve action
POST   /admin/api/ai-agent/sessions/{id}/reject/{action_id}    # Reject action

# Real-time data
GET    /admin/api/ai-agent/sessions/{id}/actions         # Stream recent actions
GET    /admin/api/ai-agent/sessions/{id}/health          # Operation health status
GET    /admin/api/ai-agent/sessions/{id}/errors          # Recent errors with context

# WebSocket for real-time updates
ws://<host>/admin/api/ai-agent/sessions/{id}/ws
```

**Configuration:**
- `AGENT_API_KEY` — shared key for CTF API ↔ agent communication
- `AI_API_BASE` — OpenAI-compatible API endpoint
- `AI_API_KEY` — AI provider API key
- `AI_MODEL` — model ID (default: gpt-4o)
- `AGENT_APPROVAL_REQUIRED` — require human approval for actions
- `AGENT_AUTO_STEP` — enable background auto-stepping
- `AGENT_MAX_STEPS` — step budget per session (default: 100)
- `WEBSOCKET_ENABLED` — enable/disable WebSocket (default: true)
- `WEBSOCKET_HEARTBEAT_INTERVAL` — heartbeat interval in seconds (default: 30)

See `/docs/AI_AGENT_UI_IMPLEMENTATION_SUMMARY.md` for detailed implementation notes.

## VM Provisioning

CTF-IT provisions and manages team-scoped VMs. Modules are selected from the event quota and applied through Ansible Semaphore.

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

`POST /admin/api/events/{id}/start` triggers auto-provisioning when `vm_quota` is present.

**Provisioning status:**

`GET /admin/api/events/{id}/provision-status` — returns aggregate progress (`total`, `creating`, `registered`, `provisioning`, `active`, `failed`) and a per-VM status list. The admin UI polls this every 5 seconds and shows a real-time progress bar and "Retry Failed" button.

### Network topology

`/admin/topology` — interactive D3 force-directed graph showing the event → team → VM hierarchy.

- Hover for IP, OS, and module progress bar
- Right-click for context menu (view details, provision, assign modules, export playbook, destroy)
- Double-click to navigate to detail page
- Live polling every 5 seconds — new nodes fade in, removed nodes fade out
- VM node border colour reflects status: green (active), amber (creating/provisioning), red (failed), grey (registered/stopped)
- Filter by event with the dropdown in the toolbar

## Ansible Export

As an alternative to Docker containers, administrators can export modules as Ansible playbooks to apply the same vulnerabilities, hardening tasks, and applications to bare machines or VMs.

### Export

```bash
# Export from an event's quota
curl -X POST https://ctf.example.com/admin/api/ \
  -H "Cookie: session=<admin_session>" \
  -H "Content-Type: application/json" \
  -d '{"event_id": 1}' \
  -o ctf-playbook.zip

# Export from an explicit quota
curl -X POST https://ctf.example.com/admin/api/ \
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

Target machines should use one of the definitions under `bases/`; Ubuntu 24.04 is the currently supplied server base.

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

## Project Structure

```
CTF-IT/
  api/                          # FastAPI application
    routes/                     # Route handlers
      auth.py                   # Registration, login, logout
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
      ssh_keys.py               # Platform SSH key management
    models.py                   # Database models
    main.py                     # App entry point
  bases/                        # VM base-type definitions and setup assets
  builder/                      # Module selection and export orchestration
    selector.py                 # Module selection (quota, conflicts, deps)
    ansible.py                  # Ansible playbook export
    caldera.py                  # Caldera plugin generation
    attack_tree.py              # Attack tree DAG construction and DFS path extraction
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
    playbook.yml.j2             # Jinja2 template for Ansible playbook export
    base_playbook.yml.j2        # VM base configuration playbook
  frontend/
    templates/                  # Jinja2 HTML templates
  playbooks/                    # Ansible playbooks for Vultr VM lifecycle
    create-vm.yml               # Provision Vultr VPS + optional Cloudflare DNS record
    destroy-vm.yml              # Destroy Vultr instance + remove DNS record
    collections/
      requirements.yml          # vultr.cloud + community.general (auto-installed by Semaphore)
  deploy/                       # Production deployment stack
    docker-compose.yml          # Traefik + Dockhand + API + Caldera + Semaphore
    .env.example                # Deployment environment variables
    caldera/
      config/
        local.yml.example       # Caldera configuration template
      plugins/
        ctf-exploit/            # Caldera plugin directory (populated by caldera-setup)
    traefik/
      traefik.yml               # Traefik static configuration
  docker-compose.yml            # Development API and disposable test service
  requirements.txt              # Python dependencies
  requirements-dev.txt          # Test-only Python dependencies
  MODULE_GUIDE.md               # Module authoring reference
  TEST_PLAN.md                  # End-to-end integration test plan
```

## Testing

Run the complete automated suite in the disposable Docker test target:

```bash
docker compose --profile test build tests
docker compose --profile test run --rm tests
```

This validates module/base definitions, selection, attack-tree behavior, goal state transitions, scoring, and quota validation. See [TEST_PLAN.md](TEST_PLAN.md) for integration boundaries and the manual infrastructure checklist. CI runs the same test image on every push and pull request.
