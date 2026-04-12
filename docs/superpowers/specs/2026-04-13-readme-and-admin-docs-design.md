# README & Admin Docs Redesign

**Date:** 2026-04-13
**Status:** Approved

## Goal

Rewrite the README as a single linear document that serves two audiences:
1. First-time admins setting up CTF-IT on a VPS from scratch
2. Returning admins looking up specific features

All content lives in `README.md`. No new files. `MODULE_GUIDE.md` stays separate and is linked from the README.

## Primary Deployment Context

Documentation is based on `deploy/docker-compose.yml` — the production stack. The root `docker-compose.yml` is development/testing only and is noted as such in the Testing section.

---

## README Structure

### Section 1: Header + Overview

- Short intro: what CTF-IT is
- Feature highlights list:
  - Docker image generation with randomised modules
  - Multi-event support with independent leaderboards
  - Blue team scoring (vulnerabilities + hardening + payloads)
  - Red team emulation via MITRE Caldera (attack trees, adversary operations, goal objectives)
  - VM auto-provisioning via Vultr + Ansible Semaphore
  - Ansible export for bare-metal deployments
  - Network topology visualisation

### Section 2: Quick Start (VPS)

Linear walkthrough for first-time setup on a VPS with a domain.

**Prerequisites:**
- VPS with Docker + Docker Compose
- Domain with DNS A record pointing to the server
- Open ports: 80, 443 (Traefik), 7010–7012, 8022, 8853, 8888 (Caldera agents)

**Steps:**
1. Clone repo
2. Configure `deploy/.env` from `deploy/.env.example`:
   - `DOMAIN` — base domain (e.g. `example.com`)
   - `ACME_EMAIL` — Let's Encrypt notifications
   - `TRAEFIK_DASHBOARD_AUTH` — generate with `echo "$(htpasswd -nB admin)" | sed 's/\$/\$\$/g'`
   - `REGISTRY_AUTH` — same generation command
   - `SEMAPHORE_ADMIN_PASSWORD`, `SEMAPHORE_POSTGRES_PASSWORD`, `SEMAPHORE_ACCESS_KEY_ENCRYPTION` — generate with `openssl rand -base64 32`
   - `CALDERA_AGENT_URL` — `http://<SERVER_IP>:8888` (public IP, not domain — agents connect directly on port 8888)
   - Optional: `VULTR_API_KEY`, `VULTR_DEFAULT_REGION`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_DOMAIN`
3. Configure root `.env` from `.env.example`:
   - `SECRET_KEY` — generate with `openssl rand -base64 32`
   - `EVENT_QUOTA` — JSON defining module selection counts
   - `ROOT_PASSWORD` — default root password baked into base images
4. Copy `deploy/caldera/config/local.yml.example` → `local.yml`, replace all `REPLACE_ME` values (`openssl rand -base64 32` for each secret)
5. Build base image: `docker build --build-arg "$(cat base/.env)" -t ctf-base:latest base/`
   - Cross-arch note: QEMU + `--platform` flag when server and users differ
6. Start: `cd deploy && docker compose up -d`
7. Services available at:
   - `ctf.DOMAIN` — CTF dashboard and API
   - `caldera.DOMAIN` — MITRE Caldera C2
   - `semaphore.DOMAIN` — Ansible Semaphore
   - `registry.DOMAIN` — Docker image registry
   - `dockhand.DOMAIN` — container management UI
   - `traefik.DOMAIN` — Traefik dashboard
8. First login to `ctf.DOMAIN` → first user auto-becomes admin. **Register before sharing the URL** — the bootstrap only applies to the very first registration on a fresh database.
9. Users configure Docker registry login before pulling images:
   `docker login registry.DOMAIN` (TLS + basic auth — no insecure-registry config needed)

### Section 3: Events & Scoring

**Event lifecycle:** `draft → open → stopped`
- Draft: invisible to users
- Open: accepts registration, users get bound to this event
- Stopped: archived, leaderboard frozen, verification blocked

**Creating an event (admin panel):**
- Name, description, welcome message, time limit (display-only)
- Quota JSON — drives module selection at registration time

**Quota JSON format:**
```json
{
  "vulnerability": {"easy": 1, "medium": 2, "hard": 1},
  "hardening": {"easy": 1, "medium": 1, "hard": 0},
  "payload": {"easy": 1, "medium": 0, "hard": 0},
  "application_external": {"easy": 1},
  "goal": {"easy": 1},
  "categories": {"authentication": 2},
  "tags": {"privilege-escalation": 1}
}
```

Valid type keys: `vulnerability`, `hardening`, `payload`, `application_external`, `application_internal`, `goal`.
`categories` and `tags` are inclusive filters — modules already selected by type/difficulty count toward these totals.

**Scoring model:**

| Score type | Source |
|---|---|
| Blue defensive | `points` for each completed `preapplied` module |
| Blue reactive | `defend_points × defend_count` per VMGoal |
| Red offensive | `red_points × achievement_count` per VMGoal |

**Scoreboard:**
- `GET /api/scoreboard?event_id=N` — blue team per-event rankings
- `GET /admin/caldera/scoreboard?event_id=N` — red vs blue combined view
- Frozen when event status = `stopped`

### Section 4: Modules

Brief summary, links to MODULE_GUIDE.md for full reference.

**Module types:**
- `vulnerability` — misconfiguration introduced at build time; users fix it
- `hardening` — users must implement a security measure from scratch
- `payload` — malicious artifact planted at build time; users find and remove it
- `application_external` / `application_internal` — infrastructure targeted by other modules; 0 points
- `goal` — red team objective (deface, C2 beacon, exfil); drives red vs blue scoring

**`stage` field** (vulnerability/payload modules only):
- `preapplied` (default) — visible on blue team dashboard, scored when fixed
- `caldera` — hidden from blue team; Caldera discovers and exploits these

**Goal module lifecycle:** `pending → achieved → defended → achieved → ...`
Each cycle increments `achievement_count` (red) or `defend_count` (blue).

Key goal YAML fields: `red_points`, `defend_points`, `verification` (detects achievement), `revert_verification` (detects revert).

→ See MODULE_GUIDE.md for full YAML reference, verification types, build steps, and examples.

### Section 5: Red Team (Caldera)

**What it does:** generates a Caldera plugin from selected modules, loads it into Caldera, and runs adversary emulation operations against blue team VMs.

**Setup:**
- `deploy/caldera/config/local.yml` — replace all `REPLACE_ME` secrets before starting
- `CALDERA_AGENT_URL` in `deploy/.env` — must be the server's **public IP** on port 8888 (agents connect directly; this port is published, not reverse-proxied)
- Caldera agent ports must be open in the firewall: 7010–7012, 8022, 8853, 8888

**Plugin export:**
- `POST /admin/caldera-export` (or admin panel) — downloads a zip of the Caldera plugin (abilities + adversary definitions) for manual upload or inspection
- Accepts `{"quota": {...}}` or `{"event_id": N}`

**One-click setup:**
- `POST /admin/caldera-setup` — generates plugin, installs it to `deploy/caldera/plugins/ctf-exploit/`, patches `local.yml` to enable it, restarts Caldera container, waits for health, creates a "CTF Red Team Emulation" operation, caches attack trees on all VMs in the event

**Attack trees:**
- DAG visualisation of modules ordered by ATT&CK kill chain phase
- Kill chain phases: infrastructure(-1) → initial-access(0) → execution(1) → persistence(2) → privilege-escalation(3) → credential-access(4) → collection(5) → impact(6) → command-and-control(7) → goal(8)
- Node colours: green=exploited, red=defended, gray=skipped, cyan=running
- Rendered on VM detail page and Caldera operation detail page

**Operations:**
- `GET/POST/DELETE /admin/caldera/operations` — CRUD
- `GET /admin/caldera/operations/{op_id}?include_tree=true` — operation results annotated on attack tree
- `GET /admin/caldera/vm-summary` — per-VM attack aggregates
- `GET /admin/caldera/vm/{vm_id}/results` — VM-specific results

**Goal state machine checks:**
- `POST /admin/vms/{vm_id}/goals/{goal_id}/check` — runs `verification` and `revert_verification` against the VM, transitions state, awards points
- Supports `http_response` verification for remote VMs; other types return 501 (not yet implemented)

### Section 6: VM Provisioning

**Teams:**
- VMs are team-scoped. Create teams per event via admin panel.
- Teams cannot be deleted while they have VMs.

**Registering an existing VM:**
- Admin panel → Teams & VMs → Register Existing
- Enter IP, SSH port/user, notes — no Vultr API key needed

**Vultr provisioning:**
- Requires `VULTR_API_KEY` + `VULTR_DEFAULT_REGION` in root `.env`
- Admin panel → Create on Vultr → choose team, OS, plan
- CTF-IT stages `playbooks/create-vm.yml`, runs via Semaphore, polls for result
- VM registers with IP; optional Cloudflare DNS A record if `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_DOMAIN` set
- Destroy: VM detail page → Destroy on Vultr → runs `destroy-vm.yml`, removes DNS record and DB record

**VM quota (auto-provisioning on event start):**

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

- `target` VMs: get modules assigned, provisioned via Ansible. Plan auto-sized from module `min_ram_mb`/`min_vcpu` requirements.
- `attacker` VMs: bare OS, no modules, go directly to `active` after creation.
- `POST /admin/events/{id}/start` triggers auto-provisioning when `vm_quota` is set.
- `GET /admin/events/{id}/provision-status` — real-time progress (total/creating/registered/provisioning/active/failed), per-VM table, "Retry Failed" button.

**Network topology:**
- `/admin/topology` — interactive D3 force-directed graph of event → team → VM hierarchy
- Live polling every 5 seconds
- Hover for IP/OS/module progress, right-click for context menu, double-click to navigate

### Section 7: Ansible Export

- Alternative to Docker images: generates Ansible playbook from selected modules for bare machines
- `POST /admin/ansible-export` — accepts `{"quota": {...}}` or `{"event_id": N}`, returns zip
- Output: `playbook.yml` + `scripts/` + `files/`
- Modules are automatically compatible — same `.sh` scripts run via `ansible.builtin.script`
- Note: write module scripts with `mkdir -p` for directories — Docker builds create parent dirs automatically but Ansible on bare machines does not

### Section 8: Project Structure

Updated to include `deploy/`, new route files, and the dev vs prod compose distinction.

Key addition: explicit note that root `docker-compose.yml` is for **development and testing only**. Production uses `deploy/docker-compose.yml`.

### Section 9: Testing

Keep TEST_PLAN.md reference and e2e script. Add note: tests run against the root compose (dev stack), not the deploy stack.

---

## What Is Not Changing

- `MODULE_GUIDE.md` — stays as-is, README links to it
- `TEST_PLAN.md` — stays as-is
- `deploy/` directory contents — docs only, no code changes

## Out of Scope

- API reference docs
- User-facing docs (how to use the CTF as a participant)
- Troubleshooting guide
