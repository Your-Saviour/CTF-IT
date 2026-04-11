# VM-Level Verification & Scoring

**Date:** 2026-04-11  
**Status:** Proposed  
**Priority:** High — foundational; other features depend on this

---

## Context

The verify/score pipeline works exclusively for Docker containers: `audit.py` runs inside a container, reads `/opt/ctf/state.json` for identity and `/root/flag.txt` for authentication, and POSTs to `host.docker.internal:8080/api/verify`. VMs have a `VMModule` table with `completed` and `completed_at` fields, but nothing ever updates them. VMs are team-scoped, so scoring must aggregate at the team level.

---

## Design

### 1. VM Authentication — Per-VM Verify Token

When modules are assigned to a VM, generate a `secrets.token_hex(32)` token stored on the VM record. This token is baked into the VM during provisioning (written to `/opt/ctf/vm_config.json`) and serves as the credential when submitting snapshots.

New columns on the `VM` model:
- `verify_token` — random token generated at module assignment time
- `build_state` — JSON blob of baseline file hashes, captured by running `audit.py` on the VM after provisioning (parallel to `UserImage.build_state`)

### 2. Submission Workflow — `submit.sh` Wrapper

`audit.py` stays collection-only (no changes). A new `submit.sh` script is written onto VMs during provisioning:

```bash
#!/bin/bash
# /opt/ctf/submit.sh — deployed to VMs during provisioning
CONFIG=/opt/ctf/vm_config.json
API_URL=$(python3 -c "import json; print(json.load(open('$CONFIG'))['api_url'])")
VM_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG'))['verify_token'])")
SNAPSHOT=$(python3 /opt/ctf/audit.py)
curl -s -X POST "$API_URL/api/vm-verify" \
  -H "Content-Type: application/json" \
  -H "X-VM-Token: $VM_TOKEN" \
  -d "$SNAPSHOT"
```

`vm_config.json` written during provisioning:
```json
{"vm_id": 1, "verify_token": "<hex>", "api_url": "https://ctf.example.com"}
```

Students run `bash /opt/ctf/submit.sh` from inside the VM — same UX as `python3 audit.py | ...` in Docker mode.

### 3. `/api/vm-verify` Endpoint

New route in `api/routes/vm_verify.py`:

- Accepts `SnapshotPayload` body (same schema as Docker verify)
- Authenticates via `X-VM-Token` header — look up VM by token, 403 if no match
- Blocks verification if the VM's event is `stopped`
- Loads `VMModule` records server-side (same pattern as `UserModule` iteration in verify.py)
- Loads `VM.build_state` for `file_hash_changed` / `password_changed` checks
- Reuses `extract_and_check()` from `api/routes/verify.py` — it is already a pure function
- Updates `VMModule.completed` and `VMModule.completed_at` on pass
- Returns the same opaque response shape as `/api/verify`

`flag_contents` verifications are not applicable to VMs — they silently return False (modules using only this type would simply never complete on VMs, but no current module does).

### 4. Provisioning Integration

In `_run_provision()` (`api/routes/vm.py`), after playbook generation, add Ansible tasks that:
1. Create `/opt/ctf/` on the target
2. Copy `audit.py` to `/opt/ctf/audit.py`
3. Write `/opt/ctf/vm_config.json` with vm_id, verify_token, api_url
4. Write `/opt/ctf/submit.sh` and `chmod +x`
5. Write `/opt/ctf/state.json` with `check_paths` and `hash_paths` derived from the VM's assigned modules (for payload module verification)
6. Run `python3 /opt/ctf/audit.py` remotely and capture output — store as `VM.build_state`

These tasks can be appended to the playbook programmatically after `render_playbook()` returns, or added as a new optional section to `templates/playbook.yml.j2`.

### 5. Team-Level Scoreboard

New endpoint `GET /api/scoreboard/teams?event_id=X`:
- Joins `Team` → `VM` → `VMModule`, aggregates `completed` count and total points per team
- Return shape: `[{rank, team_name, total_points, modules_completed, vms_count}]`

Frontend `scoreboard.html` gets a tab toggle between "Individual" (existing) and "Team" views.

---

## Files to Create / Modify

| Action | Path |
|--------|------|
| Create | `api/routes/vm_verify.py` |
| Create | `submit.sh` (static file in project root, copied during provisioning) |
| Modify | `api/models.py` — add `verify_token`, `build_state` to `VM` |
| Modify | `api/main.py` — register router, add migration for new columns |
| Modify | `api/routes/vm.py` — generate token in assign_modules, add provisioning tasks |
| Modify | `api/routes/scoreboard.py` — add team scoreboard query |
| Modify | `frontend/templates/scoreboard.html` — add team tab |
| Modify | `builder/ansible.py` or `templates/playbook.yml.j2` — ctf verification tasks |

---

## Verification / Testing

- **Unit:** import `extract_and_check`, build a `SnapshotPayload`, verify VMModule verification types work (same function already tested in `test_verify_new_types.py`)
- **Integration:** create VM, assign modules, POST to `/api/vm-verify` with valid token, assert `VMModule.completed` is set
- **Auth:** POST with missing/invalid token returns 403
- **Build state:** provision a VM, assert `VM.build_state` is populated with JSON
- **Scoreboard:** complete some VMModules, verify team endpoint returns correct aggregation

---

## Dependencies

This feature is a prerequisite for:
- **Feature 2 (Live Dashboard)** — needs `VMModule.completed` to be populated
- **Feature 3 (One-Click Setup)** — should generate `verify_token` during bulk module assignment
- **Feature 4 (Payload Modules)** — VM flow needs `state.json` with `check_paths`/`hash_paths` written during provisioning
