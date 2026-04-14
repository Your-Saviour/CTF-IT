# Module Pool Expansion — Design Spec

**Date:** 2026-04-13
**Status:** Proposed

## Problem

The module pool is too small for good randomization. With only 6 standalone vulns, 4 hardening modules, and 2 application modules, users in the same event frequently get near-identical challenge sets. The platform needs a larger, more diverse pool — especially for standalone vulnerabilities and application modules.

## Scope

- **27 new modules**: 6 application parents + 17 app-specific vulnerabilities + 8 standalone vulnerabilities
- **6 new runtimes/stacks**: Node.js, PHP/Apache, Bash, Go, Java/JRE, Next.js
- **4 new vuln categories**: network, persistence, configuration, plus expanded coverage of filesystem/services/authentication
- **Target deployment**: VPS-based (Ansible playbooks), not Docker — image size is not a constraint

## Module Directory Structure Convention

Application modules nest their sub-vulnerabilities:
```
modules/application_external/<app_id>/
├── <app_id>.yaml          # Parent app definition
├── setup.sh               # Install runtime + dependencies
├── app files...           # Source code, config, service units
├── finalize.sh            # Enable + start service
└── vulns/
    └── <vuln_id>/
        ├── <vuln_id>.yaml # Vuln definition (requires: [<app_id>])
        └── <vuln_id>.sh   # Script that introduces the vulnerability
```

Standalone vulnerabilities:
```
modules/vulns/<vuln_id>/
├── <vuln_id>.yaml
└── <vuln_id>.sh
```

---

## Application Module 1: Node.js Notes API

**Parent:** `notes_api` | Port 3000 | Runtime: Node.js 20 LTS + Express + SQLite
**Path:** `modules/application_external/notes_api/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `notes_api` | Node.js Notes API | application_external | easy | 0 | web | `process_running` process=`0.0.0.0:3000` |
| `notes_sqli` | SQL Injection in Notes Search | vulnerability | hard | 300 | web | `file_not_contains` path=`/opt/notesapi/app.js` pattern=`\+ req.query` |
| `notes_world_readable_db` | World-Readable Notes Database | vulnerability | easy | 100 | filesystem | `file_permissions` path=`/opt/notesapi/notes.db` expected=`640` |
| `notes_hardcoded_token` | Hardcoded Admin API Token | vulnerability | medium | 200 | authentication | `file_not_contains` path=`/opt/notesapi/app.js` pattern=`SuperSecret123` |
| `notes_debug_mode` | Node.js Debug Mode Enabled | vulnerability | easy | 100 | web | `file_not_contains` path=`/etc/systemd/system/notesapi.service` pattern=`DEBUG=true` |

**Setup:** `setup.sh` installs Node.js 20 via NodeSource, creates `/opt/notesapi/`, installs Express + better-sqlite3. `app.js` is a ~100-line Express REST API with `/notes`, `/search`, `/login` endpoints. `finalize.sh` creates `notesapi` user, enables systemd service.

**Hints pattern:** Vague → specific (3 hints per vuln), matching existing style.

**Conflicts:** None with existing modules. No port overlap.

---

## Application Module 2: PHP Guestbook

**Parent:** `php_guestbook` | Port 8080 | Runtime: Apache2 + PHP 8.1
**Path:** `modules/application_external/php_guestbook/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `php_guestbook` | PHP Guestbook | application_external | easy | 0 | web | `process_running` process=`apache2` |
| `guestbook_xss` | Stored XSS in Guestbook | vulnerability | medium | 200 | web | `file_not_contains` path=`/opt/guestbook/messages.txt` pattern=`<script>` |
| `guestbook_dir_listing` | Apache Directory Listing Enabled | vulnerability | easy | 100 | web | `file_not_contains` path=`/etc/apache2/sites-enabled/guestbook.conf` pattern=`+Indexes` |
| `guestbook_phpinfo` | Exposed phpinfo() Page | vulnerability | easy | 100 | web | `file_absent` path=`/opt/guestbook/phpinfo.php` |
| `guestbook_writable_config` | World-Writable Guestbook Config | vulnerability | medium | 200 | filesystem | `file_permissions` path=`/opt/guestbook/config.php` expected=`640` |
| `guestbook_apache_root` | Apache Running as Root | vulnerability | hard | 300 | services | `file_not_contains` path=`/etc/apache2/apache2.conf` pattern=`User root` |

**Setup:** `setup.sh` installs Apache2 + PHP. Copies PHP source files (index.php, config.php, phpinfo.php) to `/opt/guestbook/`. Configures Apache vhost on port 8080. `finalize.sh` enables site and restarts Apache.

**Conflicts:** None. Port 8080 not used by existing modules.

---

## Application Module 3: Bash Monitoring Daemon

**Parent:** `monitord` | Port 9000 | Runtime: Bash + netcat (already in base)
**Path:** `modules/application_external/monitord/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `monitord` | Bash Monitoring Daemon | application_external | easy | 0 | services | `process_running` process=`monitord` |
| `monitord_cmd_injection` | Command Injection in Monitord | vulnerability | hard | 300 | services | `file_not_contains` path=`/opt/monitord/monitord.sh` pattern=`eval` |
| `monitord_writable_logdir` | World-Writable Monitord Log Directory | vulnerability | easy | 100 | filesystem | `file_permissions` path=`/var/log/monitord` expected=`750` |
| `monitord_creds_in_log` | Credentials Leaked in Monitord Logs | vulnerability | medium | 200 | authentication | `file_not_contains` path=`/opt/monitord/monitord.sh` pattern=`DB_PASSWORD` |
| `monitord_suid_script` | SUID Bit on Daemon Script | vulnerability | medium | 200 | filesystem | `file_permissions` path=`/opt/monitord/monitord.sh` expected=`750` |
| `monitord_open_port` | Unauthenticated Metrics Endpoint | vulnerability | medium | 200 | network | `port_closed` port=9000 |

**Setup:** `setup.sh` creates dirs and users. `monitord.sh` is a ~60-line bash script that collects metrics via `/proc` and serves them on port 9000 via netcat. `finalize.sh` enables systemd service. Zero additional package installs needed.

**Conflicts:** None. Port 9000 not used.

---

## Application Module 4: Go File Server

**Parent:** `go_fileserver` | Port 8000 | Runtime: Pre-compiled Go binary
**Path:** `modules/application_external/go_fileserver/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `go_fileserver` | Go File Server | application_external | medium | 0 | web | `process_running` process=`0.0.0.0:8000` |
| `fileserver_path_traversal` | Path Traversal in File Server | vulnerability | hard | 300 | web | `file_contains` path=`/opt/fileserver/config.toml` pattern=`sanitize_paths = true` |
| `fileserver_default_tls_key` | World-Readable TLS Private Key | vulnerability | easy | 100 | filesystem | `file_permissions` path=`/opt/fileserver/server.key` expected=`600` |
| `fileserver_hidden_files` | Directory Listing Exposes Hidden Files | vulnerability | medium | 200 | web | `file_not_contains` path=`/opt/fileserver/config.toml` pattern=`show_hidden = true` |
| `fileserver_anon_upload` | Anonymous Upload Enabled | vulnerability | medium | 200 | web | `file_not_contains` path=`/opt/fileserver/config.toml` pattern=`allow_anonymous_upload = true` |
| `fileserver_running_as_root` | File Server Running as Root | vulnerability | medium | 200 | services | `file_contains` path=`/etc/systemd/system/fileserver.service` pattern=`User=fileserver` |

**Setup:** `setup.sh` creates `fileserver` user, copies pre-compiled binary to `/opt/fileserver/fileserver`, copies `config.toml` and TLS cert/key. `finalize.sh` enables systemd service. The Go binary is ~15MB, pre-compiled for linux/amd64 (and linux/arm64 variant). No Go toolchain installed on target.

**Build note:** The Go source lives in `modules/application_external/go_fileserver/src/` and is compiled ahead of time. The `setup.sh` only copies the binary. A Makefile or build script in the module dir handles cross-compilation.

**Conflicts:** None. Port 8000 not used.

---

## Application Module 5: Java Log Management App (Log4Shell)

**Parent:** `log4shell_app` | Port 8081 | Runtime: OpenJDK 17 JRE + pre-built JAR
**Path:** `modules/application_external/log4shell_app/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `log4shell_app` | Java Log Management App | application_external | hard | 0 | web | `process_running` process=`0.0.0.0:8081` |
| `log4shell_cve` | Log4Shell (CVE-2021-44228) | vulnerability | hard | 300 | web | `file_contains` path=`/etc/systemd/system/logapp.service` pattern=`formatMsgNoLookups=true` |
| `log4shell_actuator` | Exposed Spring Boot Actuator | vulnerability | medium | 200 | web | `file_not_contains` path=`/opt/logapp/application.properties` pattern=`exposure.include=*` |
| `log4shell_running_as_root` | Log App Running as Root | vulnerability | medium | 200 | services | `file_contains` path=`/etc/systemd/system/logapp.service` pattern=`User=logapp` |
| `log4shell_world_readable_logs` | Sensitive Logs World-Readable | vulnerability | easy | 100 | filesystem | `file_permissions` path=`/var/log/logapp` expected=`750` |

**Setup:** `setup.sh` installs OpenJDK 17 JRE headless, creates `logapp` user, copies pre-built fat JAR (with Log4j 2.14.1 bundled) to `/opt/logapp/`. `application.properties` configures Spring Boot. `finalize.sh` enables systemd service.

**Build note:** The JAR is pre-built from a vendored Maven project in `modules/application_external/log4shell_app/src/`. A simple Spring Boot app with a POST `/log` endpoint and GET `/logs` dashboard. The vulnerable Log4j 2.14.1 dependency is pinned in `pom.xml`.

**Conflicts:** None. Port 8081 not used.

---

## Application Module 6: Next.js Employee Portal (CVE-2025-29927)

**Parent:** `nextjs_portal` | Port 3001 | Runtime: Node.js 20 LTS + Next.js 14.1.0
**Path:** `modules/application_external/nextjs_portal/`

| id | name | type | difficulty | points | category | verification |
|----|------|------|-----------|--------|----------|-------------|
| `nextjs_portal` | Next.js Employee Portal | application_external | hard | 0 | web | `process_running` process=`0.0.0.0:3001` |
| `nextjs_middleware_bypass` | Middleware Auth Bypass (CVE-2025-29927) | vulnerability | hard | 300 | web | `file_not_contains` path=`/opt/portal/package.json` pattern=`"next": "14.1.0"` |
| `nextjs_unprotected_api` | Admin API Route Unprotected | vulnerability | medium | 200 | web | `file_contains` path=`/opt/portal/app/api/admin/users/route.ts` pattern=`getServerSession` |
| `nextjs_source_maps` | Source Maps Exposed in Production | vulnerability | easy | 100 | web | `file_not_contains` path=`/opt/portal/next.config.js` pattern=`productionBrowserSourceMaps: true` |
| `nextjs_hardcoded_secret` | Hardcoded Session Secret | vulnerability | easy | 100 | authentication | `file_not_contains` path=`/etc/systemd/system/portal.service` pattern=`super-secret-dev-key` |

**Setup:** `setup.sh` installs Node.js 20 (shares with `notes_api` if both present — idempotent install), copies pre-built Next.js app to `/opt/portal/`. The app is pre-built (`next build` output) so no build step on the target VM. `finalize.sh` enables systemd service.

**Conflicts:** `notes_api` and `nextjs_portal` both install Node.js 20 — this is fine, the install is idempotent. Ports 3000 and 3001 are distinct.

---

## Standalone Vulnerability Modules (8 new)

All live under `modules/vulns/<id>/` with a YAML + shell script.

### Network (2)

| id | name | difficulty | points | category | verification | setup script |
|----|------|-----------|--------|----------|-------------|-------------|
| `open_telnet` | Telnet Service Enabled | easy | 100 | network | `port_closed` port=23 | Installs `telnetd` + `xinetd`, enables service |
| `anonymous_ftp` | Anonymous FTP Access | medium | 200 | network | `file_not_contains` path=`/etc/vsftpd.conf` pattern=`anonymous_enable=YES` | Installs `vsftpd`, sets `anonymous_enable=YES` |

### Privilege Escalation (2)

| id | name | difficulty | points | category | verification | setup script |
|----|------|-----------|--------|----------|-------------|-------------|
| `suid_python` | SUID Bit on Python3 | medium | 200 | filesystem | `file_permissions` path=`/usr/bin/python3` expected=`755` | `chmod u+s /usr/bin/python3` |
| `writable_passwd` | World-Writable /etc/passwd | hard | 300 | filesystem | `file_permissions` path=`/etc/passwd` expected=`644` | `chmod 666 /etc/passwd` |

### Persistence (2)

| id | name | difficulty | points | category | verification | setup script |
|----|------|-----------|--------|----------|-------------|-------------|
| `malicious_cron_beacon` | Malicious Cron Job (C2 Beacon) | medium | 200 | persistence | `cron_not_present` pattern=`beacon.sh` | Adds `*/5 * * * * /opt/.hidden/beacon.sh` to root crontab, creates dummy beacon script |
| `backdoor_bashrc` | Backdoor in Root .bashrc | easy | 100 | persistence | `file_not_contains` path=`/root/.bashrc` pattern=`nc -e /bin/bash` | Appends reverse shell one-liner to `.bashrc` |

### Configuration (2)

| id | name | difficulty | points | category | verification | setup script |
|----|------|-----------|--------|----------|-------------|-------------|
| `ip_forwarding_enabled` | IP Forwarding Enabled | easy | 100 | configuration | `file_not_contains` path=`/etc/sysctl.conf` pattern=`net.ipv4.ip_forward = 1` | Sets `net.ipv4.ip_forward = 1` in sysctl.conf and applies with `sysctl -p` |
| `core_dumps_unrestricted` | Unrestricted Core Dumps | medium | 200 | configuration | `file_contains` path=`/etc/security/limits.conf` pattern=`hard core 0` | Removes core dump restriction, sets `fs.suid_dumpable = 2` |

---

## Summary

### New Module Counts

| Type | New Parents | New Vulns | Total New |
|------|-----------|-----------|-----------|
| application_external | 6 | 0 | 6 |
| vulnerability (app-specific) | 0 | 27 | 27 |
| vulnerability (standalone) | 0 | 8 | 8 |
| **Total** | **6** | **35** | **41** |

*Note: The 6 app parents are scored at 0 points. The 27 app-specific vulns each `require` their parent app.*

### Difficulty Distribution (scoreable modules only — 35 new)

| Difficulty | Count | Percentage |
|-----------|-------|-----------|
| Easy | 12 | 34% |
| Medium | 16 | 46% |
| Hard | 7 | 20% |

### Category Coverage (after expansion)

| Category | Existing | New | Total |
|----------|---------|-----|-------|
| web | 5 | 14 | 19 |
| filesystem | 3 | 7 | 10 |
| authentication | 4 | 3 | 7 |
| services | 1 | 4 | 5 |
| network | 1 | 3 | 4 |
| persistence | 0 | 2 | 2 |
| configuration | 0 | 2 | 2 |

### Port Allocation (all apps)

| Port | Application |
|------|------------|
| 5000 | Vulnerable Flask App (existing) |
| 5001 | Inventory Dashboard (existing) |
| 3000 | Node.js Notes API (new) |
| 3001 | Next.js Employee Portal (new) |
| 8000 | Go File Server (new) |
| 8080 | PHP Guestbook (new) |
| 8081 | Java Log Management App (new) |
| 9000 | Bash Monitoring Daemon (new) |

### Runtime Dependencies

| Runtime | Installed by | Approx size | Shared by |
|---------|-------------|-------------|-----------|
| Node.js 20 LTS | `notes_api`, `nextjs_portal` | ~80MB | Both Node apps (idempotent install) |
| Apache2 + PHP 8.1 | `php_guestbook` | ~60MB | — |
| OpenJDK 17 JRE | `log4shell_app` | ~200MB | — |
| Go binary (pre-compiled) | `go_fileserver` | ~15MB | — |
| Bash + netcat | `monitord` | 0 (in base) | — |

### Conflict Map

No port conflicts between any modules. No file path conflicts. The only shared dependency is Node.js between `notes_api` and `nextjs_portal` (idempotent).

`suid_python` sets SUID on `/usr/bin/python3` which is used by `audit.py`, but SUID does not prevent normal execution — no functional conflict.

---

## Verification

### Per-module testing
Each module should be tested individually:
1. Create a fresh VM (or Docker container for dev)
2. Run the app's `setup.sh` + `finalize.sh`
3. Run the vuln's `.sh` script
4. Verify the vulnerability is present (verification should fail)
5. Apply the fix described in `suggested_fix`
6. Verify the fix works (verification should pass)

### Integration testing
1. Configure an `EVENT_QUOTA` that selects modules from all new types
2. Run the selector — verify no conflict errors and dependency resolution works
3. Generate an Ansible playbook export
4. Apply to a fresh VM
5. Run `audit.py` → POST to `/api/verify` → confirm scoring works

### Quota example for full coverage testing
```json
{
  "vulnerability": {"easy": 5, "medium": 8, "hard": 4},
  "hardening": {"easy": 2, "medium": 2, "hard": 0},
  "application_external": {"easy": 3, "medium": 1, "hard": 2}
}
```
