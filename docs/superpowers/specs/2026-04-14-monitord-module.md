# Bash Monitoring Daemon — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A bash-based system monitoring daemon that collects metrics via `/proc` and serves them over a raw TCP port (9000) using netcat. Requires zero additional package installs beyond what is already in the base image. Introduces 5 vulnerabilities covering command injection, world-writable log directory, credentials in logs, SUID script, and unauthenticated metrics endpoint.

| Field | Value |
|-------|-------|
| Port | 9000 |
| Runtime | Bash + netcat (pre-installed in base) |
| Path | `modules/application_external/monitord/` |
| Service user | `monitord` |
| Install dir | `/opt/monitord/` |
| Log dir | `/var/log/monitord/` |

---

## Directory Structure

```
modules/application_external/monitord/
├── monitord.yaml
├── setup.sh
├── finalize.sh
├── monitord.sh
├── monitord.service
└── vulns/
    ├── monitord_cmd_injection/
    │   ├── monitord_cmd_injection.yaml
    │   └── monitord_cmd_injection.sh
    ├── monitord_writable_logdir/
    │   ├── monitord_writable_logdir.yaml
    │   └── monitord_writable_logdir.sh
    ├── monitord_creds_in_log/
    │   ├── monitord_creds_in_log.yaml
    │   └── monitord_creds_in_log.sh
    ├── monitord_suid_script/
    │   ├── monitord_suid_script.yaml
    │   └── monitord_suid_script.sh
    └── monitord_open_port/
        ├── monitord_open_port.yaml
        └── monitord_open_port.sh
```

---

## Module Definitions

### Parent: `monitord.yaml`

```yaml
id: monitord
name: Bash Monitoring Daemon
description: A lightweight system monitoring daemon written in bash. Collects CPU, memory, and disk metrics from /proc and serves them over a TCP socket on port 9000 using netcat.
type: application_external
difficulty: easy
points: 0
category: services
tags: [services, bash, monitoring, daemon]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: monitord
  expected: running
hints:
  - "Check what custom services are running on this machine"
```

---

### Vuln 1: `monitord_cmd_injection.yaml`

```yaml
id: monitord_cmd_injection
name: Command Injection in Monitord
description: The monitoring daemon uses eval to process query parameters received over the network, allowing an attacker to inject arbitrary shell commands via the metrics endpoint.
type: vulnerability
difficulty: hard
points: 300
category: services
tags: [command-injection, bash, services, rce]
conflicts: []
requires: [monitord]
script: monitord_cmd_injection.sh
verification:
  type: file_not_contains
  path: /opt/monitord/monitord.sh
  pattern: "eval"
suggested_fix: "Remove the eval statement from /opt/monitord/monitord.sh. Replace dynamic command execution with a fixed case/esac block that maps known query strings to specific safe commands. Restart the service after editing."
hints:
  - "Review the monitoring daemon script for unsafe input processing"
  - "Look for dangerous shell constructs in /opt/monitord/monitord.sh"
  - "Replace eval with a case statement that maps specific metric names to hardcoded commands, then restart monitord"
caldera:
  tactic: execution
  technique:
    attack_id: T1059.004
    name: "Command and Scripting Interpreter: Unix Shell"
  recon:
    description: "Check if monitord uses eval for input processing"
    command: |
      grep -q "eval" /opt/monitord/monitord.sh && echo "VULNERABLE: eval detected in monitord.sh" || echo "SECURE: no eval in monitord.sh"
  exploit:
    description: "Inject a command via the metrics TCP endpoint"
    command: |
      echo "cpu; id" | nc -q 1 localhost 9000
```

---

### Vuln 2: `monitord_writable_logdir.yaml`

```yaml
id: monitord_writable_logdir
name: World-Writable Monitord Log Directory
description: The daemon's log directory at /var/log/monitord is world-writable, allowing any local user to delete, overwrite, or plant log entries.
type: vulnerability
difficulty: easy
points: 100
category: filesystem
tags: [permissions, filesystem, logging]
conflicts: []
requires: [monitord]
script: monitord_writable_logdir.sh
verification:
  type: file_permissions
  path: /var/log/monitord
  expected: "750"
suggested_fix: "chmod 750 /var/log/monitord && chown monitord:monitord /var/log/monitord"
hints:
  - "Check permissions on log directories for running services"
  - "Look at the permissions on /var/log/monitord"
  - "Use chmod 750 to restrict the log directory to the service owner and group"
caldera:
  tactic: defense-evasion
  technique:
    attack_id: T1070.002
    name: "Indicator Removal: Clear Linux or Mac System Logs"
  recon:
    description: "Check if the monitord log directory is world-writable"
    command: |
      [ -w /var/log/monitord ] && stat -c "%a" /var/log/monitord | grep -qE "^[0-9][0-9][2-7]$" && echo "VULNERABLE: /var/log/monitord is world-writable" || echo "SECURE: log directory permissions are restricted"
  exploit:
    description: "Tamper with or delete monitoring logs as an unprivileged user"
    command: |
      ls /var/log/monitord/
      echo "TAMPERED" > /var/log/monitord/monitord.log
      echo "Log file overwritten successfully"
```

---

### Vuln 3: `monitord_creds_in_log.yaml`

```yaml
id: monitord_creds_in_log
name: Credentials Leaked in Monitord Logs
description: The monitoring daemon script logs the DB_PASSWORD environment variable in plaintext to its log file when the service starts, leaking credentials to anyone who can read the logs.
type: vulnerability
difficulty: medium
points: 200
category: authentication
tags: [credentials, logging, information-disclosure, bash]
conflicts: []
requires: [monitord]
script: monitord_creds_in_log.sh
verification:
  type: file_not_contains
  path: /opt/monitord/monitord.sh
  pattern: "DB_PASSWORD"
suggested_fix: "Remove the line in /opt/monitord/monitord.sh that logs DB_PASSWORD, and rotate the log files: > /var/log/monitord/monitord.log. Restart the service."
hints:
  - "Review what the monitoring daemon logs at startup"
  - "Search /opt/monitord/monitord.sh for any logging of sensitive environment variables"
  - "Remove the DB_PASSWORD logging line from monitord.sh, clear the log file, and restart the service"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1552.003
    name: "Unsecured Credentials: Bash History"
  recon:
    description: "Check if DB_PASSWORD is referenced in the monitord script"
    command: |
      grep -q "DB_PASSWORD" /opt/monitord/monitord.sh && echo "VULNERABLE: credentials logged in monitord.sh" || echo "SECURE: no credential logging found"
  exploit:
    description: "Extract the database password from monitord logs"
    command: |
      grep -i "password\|DB_PASS\|credential" /var/log/monitord/monitord.log 2>/dev/null | head -5
```

---

### Vuln 4: `monitord_suid_script.yaml`

```yaml
id: monitord_suid_script
name: SUID Bit on Daemon Script
description: The monitord.sh script has the SUID bit set, meaning any user who can execute it will run it with root privileges. The script's bash functionality can be trivially abused for privilege escalation.
type: vulnerability
difficulty: medium
points: 200
category: filesystem
tags: [suid, permissions, privilege-escalation, bash]
conflicts: []
requires: [monitord]
script: monitord_suid_script.sh
verification:
  type: file_permissions
  path: /opt/monitord/monitord.sh
  expected: "750"
suggested_fix: "chmod 750 /opt/monitord/monitord.sh — SUID is meaningless on shell scripts in Linux (the kernel ignores it), but the bit should be removed to avoid confusion and pass audits: chmod u-s /opt/monitord/monitord.sh"
hints:
  - "Check for unusual permission bits on service scripts"
  - "Look at the permissions on /opt/monitord/monitord.sh"
  - "Remove the SUID bit: chmod u-s /opt/monitord/monitord.sh (or chmod 750)"
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548.001
    name: "Abuse Elevation Control Mechanism: Setuid and Setgid"
  recon:
    description: "Check if monitord.sh has the SUID bit set"
    command: |
      test -u /opt/monitord/monitord.sh && echo "VULNERABLE: SUID bit set on monitord.sh" || echo "SECURE: no SUID bit on monitord.sh"
  exploit:
    description: "Confirm SUID bit presence on the daemon script"
    command: |
      stat -c "%a %U %G" /opt/monitord/monitord.sh
      ls -la /opt/monitord/monitord.sh
```

---

### Vuln 5: `monitord_open_port.yaml`

```yaml
id: monitord_open_port
name: Unauthenticated Metrics Endpoint
description: The monitoring daemon listens on port 9000 with no authentication, allowing any network-reachable host to query system metrics including CPU usage, memory, running processes, and disk layout.
type: vulnerability
difficulty: medium
points: 200
category: network
tags: [network, information-disclosure, unauthenticated, services]
conflicts: []
requires: [monitord]
script: monitord_open_port.sh
verification:
  type: port_closed
  port: 9000
suggested_fix: "Stop the monitord service and disable it: systemctl stop monitord && systemctl disable monitord. Alternatively, add firewall rules to block external access to port 9000: ufw deny 9000/tcp"
hints:
  - "Check what ports this machine is listening on"
  - "Test whether port 9000 is accessible without any credentials"
  - "Stop and disable the monitord service (systemctl stop monitord && systemctl disable monitord) or block port 9000 with a firewall rule"
caldera:
  tactic: collection
  technique:
    attack_id: T1046
    name: "Network Service Discovery"
  recon:
    description: "Check if port 9000 is listening and unauthenticated"
    command: |
      ss -tlnp | grep -q ":9000" && echo "VULNERABLE: port 9000 is open" || echo "SECURE: port 9000 is not listening"
  exploit:
    description: "Query system metrics from the unauthenticated endpoint"
    command: |
      echo "cpu" | nc -q 1 localhost 9000
      echo "mem" | nc -q 1 localhost 9000
      echo "disk" | nc -q 1 localhost 9000
```

---

## Setup Scripts

### `setup.sh`

1. Create `/opt/monitord/` directory.
2. Create `/var/log/monitord/` directory.
3. Copy `monitord.sh` to `/opt/monitord/monitord.sh`.
4. Copy `monitord.service` to `/etc/systemd/system/monitord.service`.
5. No package installs needed — bash and netcat are in the base image.

### `finalize.sh`

1. Create `monitord` system user: `useradd -r -s /bin/false monitord`
2. Set ownership: `chown -R monitord:monitord /opt/monitord/ /var/log/monitord/`
3. Set permissions: `chmod 750 /opt/monitord/monitord.sh && chmod 750 /var/log/monitord/`
4. `systemctl daemon-reload && systemctl enable --now monitord`

---

## Application Source (`monitord.sh`)

~60-line bash script:

```bash
#!/bin/bash
# Monitord - System Metrics Daemon

LOGFILE=/var/log/monitord/monitord.log
PORT=9000

log() { echo "$(date '+%Y-%m-%d %T') $*" >> "$LOGFILE"; }

collect_cpu() {
    awk '/cpu / {usage=100-($5*100/($2+$3+$4+$5+$6+$7+$8)); printf "cpu_usage=%.1f\n", usage}' /proc/stat
}

collect_mem() {
    awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "mem_used_kb=%d\nmem_total_kb=%d\n", t-a, t}' /proc/meminfo
}

collect_disk() {
    df -h / | awk 'NR==2{printf "disk_used=%s\ndisk_total=%s\ndisk_pct=%s\n", $3, $2, $5}'
}

handle_request() {
    local query="$1"
    case "$query" in
        cpu)  collect_cpu ;;
        mem)  collect_mem ;;
        disk) collect_disk ;;
        *)    echo "unknown metric" ;;
    esac
}

log "monitord starting on port $PORT"

while true; do
    request=$(echo "" | nc -l -p "$PORT" -q 1)
    response=$(handle_request "$(echo "$request" | tr -d '\r\n')")
    echo "$response" | nc -l -p "$PORT" -q 1 > /dev/null 2>&1 &
done
```

**Secure baseline:** uses `case` statement, no `eval`, no credential logging, log dir 750, script perms 750.

**Vuln scripts:**
- `monitord_cmd_injection.sh`: replaces `case` block with `eval "$query"` in `handle_request`
- `monitord_creds_in_log.sh`: adds `log "DB_PASSWORD=${DB_PASSWORD:-changeme456}"` to startup
- `monitord_writable_logdir.sh`: `chmod 777 /var/log/monitord`
- `monitord_suid_script.sh`: `chmod u+s /opt/monitord/monitord.sh`
- `monitord_open_port.sh`: no-op — the port is already open by the parent app; the vuln script just ensures the service is running

---

## `monitord.service`

```ini
[Unit]
Description=Monitord System Metrics Daemon
After=network.target

[Service]
Type=simple
User=monitord
ExecStart=/bin/bash /opt/monitord/monitord.sh
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/monitord/monitord.log
StandardError=append:/var/log/monitord/monitord.log

[Install]
WantedBy=multi-user.target
```

---

## Verification Checklist

1. **monitord**: Run `setup.sh` + `finalize.sh` → `systemctl is-active monitord` → `active`. `echo "cpu" | nc -q 1 localhost 9000` → returns CPU metrics.
2. **monitord_cmd_injection**: Run script → `echo "cpu; id" | nc -q 1 localhost 9000` → shows `uid=0(root)` or similar. Fix: revert to `case` block → retest, `id` not executed.
3. **monitord_writable_logdir**: Run script → `stat -c "%a" /var/log/monitord` → `777`. Fix: `chmod 750` → `750`.
4. **monitord_creds_in_log**: Run script → `grep DB_PASSWORD /var/log/monitord/monitord.log` → found. Fix: remove from script, clear log → grep returns nothing.
5. **monitord_suid_script**: Run script → `ls -la /opt/monitord/monitord.sh` → shows `s` bit. Fix: `chmod u-s` → no `s` bit.
6. **monitord_open_port**: Service running → `ss -tlnp | grep 9000` → listening. Fix: `systemctl stop monitord && systemctl disable monitord` → port closed.

---

## Port / Conflict Notes

- Port 9000 is not used by any existing module.
- No package installs — zero disk footprint beyond the scripts themselves.
- No file path conflicts with existing modules.
