# Red-Team VM Attack Model

## Context

The current Caldera integration deploys a Sandcat agent directly onto each target VM and runs exploitation commands locally. This is unrealistic -- in a real engagement, a red team operates from their own machine, attacking targets remotely. This plan introduces a dedicated red-team VM per team that launches attacks against the team's target VMs using a two-phase kill chain.

**Design decisions:**
- Red-team VM is admin-registered (one per team)
- Phase 1: Network-based remote attacks from red-team VM
- Phase 2: Deploy Sandcat on target after gaining access, run post-exploitation locally
- Module YAML gets a new `caldera.remote` section for remote attack commands
- Existing local agent flow kept as optional (Phase 2)

---

## Step 1: Data Model Changes

**Files:** `api/models.py`, `api/database.py`

Add `role` column to `VM` model:
```python
role: Mapped[str] = mapped_column(String(16), default="target")
# Valid: "target" (default), "red-team"
```

Add Caldera operation tracking to `Team` model:
```python
caldera_phase1_op_id: Mapped[str] = mapped_column(String(64), nullable=True)
caldera_phase2_op_id: Mapped[str] = mapped_column(String(64), nullable=True)
```

In `init_db()`, add startup migration for existing databases (SQLite `ALTER TABLE ADD COLUMN` for each new column if missing, using `inspect(engine).get_columns()`).

---

## Step 2: Module YAML Updates

**Files:** 7 module YAML files under `modules/`

Add optional `caldera.remote` section with commands designed to run FROM the red-team VM. Uses Caldera fact syntax `#{target.ip}` for dynamic targeting.

Example (`inventory_default_creds.yaml`):
```yaml
caldera:
  tactic: initial-access
  technique:
    attack_id: T1078.001
    name: "Valid Accounts: Default Accounts"
  recon:
    description: "Check if default admin credentials exist on inventory dashboard"
    command: |
      curl -s -o /dev/null -w '%{http_code}' -X POST -d 'username=admin&password=admin' http://localhost:5001/login | grep -q '302' && echo "VULNERABLE" || echo "SECURE"
  exploit:
    description: "Authenticate with default admin credentials"
    command: |
      curl -s -c /tmp/cookies.txt -X POST -d 'username=admin&password=admin' http://localhost:5001/login && curl -s -b /tmp/cookies.txt http://localhost:5001/ | head -20
  remote:
    recon:
      description: "Remotely check if default credentials work on target"
      command: |
        curl -s -o /dev/null -w '%{http_code}' -X POST -d 'username=admin&password=admin' http://#{target.ip}:5001/login | grep -q '302' && echo "VULNERABLE on #{target.ip}" || echo "SECURE"
    exploit:
      description: "Remotely authenticate with default admin credentials"
      command: |
        curl -s -c /tmp/cookies_#{target.ip}.txt -X POST -d 'username=admin&password=admin' http://#{target.ip}:5001/login && curl -s -b /tmp/cookies_#{target.ip}.txt http://#{target.ip}:5001/ | head -20
      cleanup: |
        rm -f /tmp/cookies_#{target.ip}.txt
```

Modules getting `remote` sections:
| Module | Remote Attack Type |
|--------|-------------------|
| `inventory_default_creds` | HTTP POST to `#{target.ip}:5001/login` with admin/admin |
| `inventory_debug_mode` | HTTP GET to `#{target.ip}:5001/console` |
| `flask_defacement` | HTTP GET to `#{target.ip}:5000/` |
| `inventory_backup_file` | HTTP GET to exposed backup path on `#{target.ip}` |
| `inventory_unrestricted_upload` | HTTP POST multipart to `#{target.ip}:5001/upload` |
| `unauthorized_ssh_key` | `ssh -i /tmp/rogue_key root@#{target.ip}` (rogue key as payload) |

Modules WITHOUT `remote` (post-exploitation only -- Phase 2):
- `suid_find`, `nopasswd_sudo`, `world_writable_shadow`, `writable_cron_script`, `inventory_secret_key`

---

## Step 3: Plugin Generation Changes

**File:** `builder/caldera.py`

Modify `_build_abilities()`:
- Check for `cal.get("remote")` on each module
- Generate up to 4 abilities per module: Remote Recon, Remote Exploit (from `caldera.remote`), Local Recon, Local Exploit (from existing `caldera.recon`/`caldera.exploit`)
- Remote abilities named `"Remote Recon: {name}"` / `"Remote Exploit: {name}"`
- Extend `_ability_uuid()` to include context: `f"{module_id}_{phase}_remote"` vs `f"{module_id}_{phase}"`
- Tag abilities with a metadata marker (description prefix or tactic annotation) so the setup endpoint can distinguish them

Modify `_build_adversary_profiles()`:
- **"CTF Phase 1 - Remote Attack"**: Only remote abilities (recon then exploit). For red-team agent group.
- **"CTF Phase 2 - Post-Exploitation"**: Only local abilities. For target agent group.
- **"CTF Full Exploit Chain"**: All abilities in kill chain order (remote recon -> remote exploit -> local recon -> local exploit).
- Keep per-tactic profiles, updated to include remote variants.

Add fact source generation function:
- `generate_fact_source(team_name, target_vm_ips) -> dict` returns a Caldera fact source payload with `target.ip` facts for each target VM IP.

---

## Step 4: Caldera Setup Changes

**File:** `api/routes/caldera_setup.py`

Modify `caldera_setup()` endpoint to accept optional `team_id`:
- When `team_id` provided: create team-specific fact source via `POST /api/v2/sources` with target VM IPs
- Create **two operations per team**:
  - Phase 1: adversary = "CTF Phase 1 - Remote Attack", group = `"red"`, source = team fact source
  - Phase 2: adversary = "CTF Phase 2 - Post-Exploitation", group = `"target"`, source = default
- Store operation IDs on `Team.caldera_phase1_op_id` and `caldera_phase2_op_id`
- Without `team_id`: existing behavior (single operation, backward compat)

New helper functions:
- `_create_team_fact_source(api_key, team, target_vms) -> str`
- `_create_phase_operation(api_key, adversary_id, planner_id, source_id, group, name) -> dict`

---

## Step 5: API Endpoint Changes

**File:** `api/routes/vm.py`

**VM CRUD:**
- `create_vm()`: Accept `role` field (default `"target"`). Enforce max one red-team VM per team.
- `update_vm()`: Allow changing `role`. Same uniqueness check.
- VM serialization: Include `role` in response dicts.

**Agent deployment:**
- `_run_deploy_agent()`: Dynamic agent group based on `vm.role`:
  - `"red-team"` -> group `"red"`
  - `"target"` -> group `"target"`
- Change the hardcoded `caldera_group = "red"` (line 734) to be role-dependent.

**New endpoint:**
- `GET /admin/teams/{team_id}/red-team-config` -- Returns team's red-team VM, target VMs, and Caldera operation status. Powers the UI.

---

## Step 6: UI Changes

**File:** `frontend/templates/vm_detail.html`
- Role badge next to hostname ("RED-TEAM" in red, or "TARGET" default)
- Role dropdown/toggle to switch between target and red-team
- Contextual deploy button label: "Deploy Attack Agent" (red-team) vs "Deploy Post-Exploit Agent" (target)
- When viewing red-team VM: show list of team's target VMs with IPs

**File:** `frontend/templates/admin.html`
- Caldera setup button: enhanced to create per-team operations with fact sources
- Team list: show red-team VM assignment, operation status

---

## Implementation Order

1. **Models + migration** (Step 1) -- foundation, no breaking changes
2. **Module YAML updates** (Step 2) -- can be done independently
3. **Plugin generation** (Step 3) -- depends on Step 2 for remote metadata
4. **Caldera setup** (Step 4) -- depends on Step 3 for new adversary profiles
5. **API endpoints** (Step 5) -- depends on Step 1 for role column
6. **UI** (Step 6) -- depends on Steps 4-5 for API

Steps 1+2 can be done in parallel. Steps 3+5 can be done in parallel after their deps.

---

## Verification

1. **Model migration**: Start app with existing DB, verify `role` column added with default `"target"` for existing VMs
2. **Module YAML**: Load modules via `load_all_modules()`, verify `caldera.remote` parsed correctly
3. **Plugin generation**: Run `generate_caldera_export()`, verify output contains both remote and local abilities, separate adversary profiles
4. **Caldera setup**: Call `POST /admin/caldera-setup` with `team_id`, verify:
   - Fact source created with target VM IPs
   - Phase 1 operation targets `red` group
   - Phase 2 operation targets `target` group
5. **End-to-end**: Register red-team VM for a team -> deploy attack agent -> run Caldera setup with team_id -> verify Phase 1 operation runs remote abilities on red-team agent using `#{target.ip}` substitution
6. **Backward compat**: Caldera setup without `team_id` still creates single operation as before

---

## Key Files to Modify

| File | Changes |
|------|---------|
| `api/models.py` | Add `VM.role`, `Team.caldera_phase1_op_id`, `Team.caldera_phase2_op_id` |
| `api/database.py` | Startup migration for new columns |
| `builder/caldera.py` | Remote abilities, dual adversary profiles, fact source generation |
| `api/routes/caldera_setup.py` | Team-scoped fact sources, two-phase operations |
| `api/routes/vm.py` | Role in CRUD, dynamic agent group, new endpoint |
| `frontend/templates/vm_detail.html` | Role badge, contextual buttons, target list |
| `frontend/templates/admin.html` | Enhanced Caldera setup, team status |
| 7 module YAML files | Add `caldera.remote` sections |
