# Module Guide

How to create new vulnerability, hardening, and application modules for CTF-IT.

## Folder Structure

Each module lives in its own folder under `modules/vulns/`, `modules/hardening/`, or `modules/application/`:

```
modules/
  vulns/<module_id>/
    <module_id>.yaml        # Required: module definition
    <module_id>.sh          # Optional: shell script to introduce the vulnerability
  hardening/<module_id>/
    <module_id>.yaml        # Required: module definition
  application/<module_id>/
    <module_id>.yaml        # Required: module definition
    *.sh                    # Shell scripts referenced by steps or script field
    *.py, *.conf, etc.      # Files referenced by copy steps
```

- **Vulnerability** modules include a shell script that introduces a misconfiguration during the Docker build. The user must fix it.
- **Hardening** modules usually don't have a script — the user is expected to implement the fix from scratch.
- **Application** modules install infrastructure (web apps, services, CLI tools) that vulnerability modules can target via `requires`. They typically award 0 points.

## YAML Reference

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique snake_case identifier. Must match the folder name. |
| `name` | string | Human-readable display name. |
| `description` | string | What the issue is and why it matters. Shown to users as their task. |
| `type` | string | `vulnerability`, `hardening`, or `application` |
| `difficulty` | string | `easy`, `medium`, or `hard` |
| `points` | integer | Points awarded on completion. |
| `category` | string | Grouping category (e.g. `filesystem`, `services`, `network`, `authentication`). |
| `verification` | object | How to check if the user fixed the issue. See [Verification Types](#verification-types). |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tags` | list[string] | `[]` | Searchable tags for filtering. |
| `conflicts` | list[string] | `[]` | Module IDs that cannot coexist with this module. |
| `requires` | list[string] | `[]` | Module IDs that must also be selected if this module is picked. |
| `script` | string | `null` | Filename of a single shell script (legacy — use `steps` for new modules). |
| `steps` | list | `[]` | Ordered build steps. See [Build Steps](#build-steps). Replaces `script`. |
| `hints` | list[string] | `[]` | Progressive hints shown to users. Order from vague to specific. |
| `suggested_fix` | string | `null` | The command(s) that fix the issue. Used for admin reference/testing. |

## Build Steps

The `steps` field defines an ordered list of build operations. Each step is either a file copy or a script execution:

### `run` — Execute a shell script

```yaml
steps:
  - run: setup.sh
```

Runs a `.sh` file from the module directory during the Docker build. Same behavior as the legacy `script` field.

### `copy` — Copy a file into the container

```yaml
steps:
  - copy: { src: app.py, dest: /opt/myapp/app.py }
  - copy: { src: config.ini, dest: /etc/myapp/config.ini, mode: "0644" }
```

Copies a file from the module directory to the specified path in the container. The optional `mode` field sets permissions via `chmod` after copying.

### Multi-stage example

Steps execute in order, so you can interleave copies and scripts:

```yaml
steps:
  - run: install_deps.sh              # apt/pip install
  - copy: { src: app.py, dest: /opt/myapp/app.py }
  - copy: { src: myapp.service, dest: /etc/systemd/system/myapp.service }
  - run: finalize.sh                   # DB init, systemd enable, etc.
```

### Legacy `script` field

Modules using `script: some_file.sh` continue to work — it is automatically converted to `steps: [{run: some_file.sh}]` during loading. New modules should use `steps` instead.

## Verification Types

The `verification` field defines how the server checks whether the user has completed the task. The in-container `audit.py` collects a broad system snapshot, and the server matches it against the module's verification spec.

### `file_permissions`

Checks that a file has the correct permissions.

```yaml
verification:
  type: file_permissions
  path: /etc/shadow
  expected: "640"
```

The collector also gathers `owner` and `group` metadata.

### `file_contains`

Checks that a file contains a specific pattern.

```yaml
verification:
  type: file_contains
  path: /etc/ssh/sshd_config
  pattern: "PermitRootLogin no"
  expected: true
```

### `file_not_contains`

Checks that a file does NOT contain a specific pattern.

```yaml
verification:
  type: file_not_contains
  path: /etc/ssh/sshd_config
  pattern: "PermitRootLogin yes"
  expected: true
```

### `service_running`

Checks that a systemd service is active.

```yaml
verification:
  type: service_running
  service: fail2ban
  expected: active
```

### `package_installed`

Checks that a package is installed via dpkg.

```yaml
verification:
  type: package_installed
  package: fail2ban
  expected: true
```

### `port_closed`

Checks that a port is NOT listening.

```yaml
verification:
  type: port_closed
  port: 23
  expected: true
```

### `flag_contents`

Checks the contents of a file (typically the flag file).

```yaml
verification:
  type: flag_contents
  path: /root/flag.txt
```

### `password_not_default`

Checks that a user's password has been changed from the default (i.e. a real hash exists, not `!`, `*`, etc.).

```yaml
verification:
  type: password_not_default
  user: root
  expected: false
```

### `password_changed`

Checks that a user's password hash differs from the one set at image build time. The original hash is automatically captured and injected into the manifest during the Docker build. Use this instead of `password_not_default` when the base image already sets a real password.

```yaml
verification:
  type: password_changed
  user: root
```

### `http_response`

Checks HTTP status code and/or body content from a running web application. The `label` is `localhost_<port>` matching the port the app listens on. Supports `status_code`, `body_contains`, and `body_not_contains` — all optional, but at least one should be specified.

```yaml
verification:
  type: http_response
  label: localhost_5000
  status_code: 200
  body_not_contains: "HACKED BY"
```

The audit script dynamically probes all listening ports for HTTP responses.

### `process_running`

Checks whether a process matching a substring pattern is running. Use `expected: running` (default) to check presence, or `expected: stopped` to check absence.

```yaml
verification:
  type: process_running
  process: gunicorn
  expected: running
```

The pattern is matched as a substring against the full command string from `ps aux`.

## Examples

### Vulnerability Module

```
modules/vulns/world_writable_shadow/
  world_writable_shadow.yaml
  world_writable_shadow.sh
```

**world_writable_shadow.yaml**:

```yaml
id: world_writable_shadow
name: World-writable /etc/shadow
description: The /etc/shadow file has incorrect permissions, allowing any user to read or modify password hashes.
type: vulnerability
difficulty: medium
points: 200
category: filesystem
tags: [permissions, shadow, authentication]
conflicts: []
requires: []
script: world_writable_shadow.sh
verification:
  type: file_permissions
  path: /etc/shadow
  expected: "640"
suggested_fix: "chmod 640 /etc/shadow && chown root:shadow /etc/shadow"
hints:
  - "Check the permissions on sensitive authentication files"
  - "Use chmod and chown to correct /etc/shadow"
```

**world_writable_shadow.sh**:

```bash
#!/bin/bash
chmod 666 /etc/shadow
```

The script runs during the Docker image build to introduce the vulnerability. The user must then fix it inside their running container.

### Hardening Module

```
modules/hardening/install_fail2ban/
  install_fail2ban.yaml
```

**install_fail2ban.yaml**:

```yaml
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
suggested_fix: "apt-get update && apt-get install -y fail2ban && systemctl start fail2ban && systemctl enable fail2ban"
hints:
  - "Look into intrusion prevention tools available via apt"
```

Hardening modules have no script — the base image is clean and the user must implement the hardening measure themselves.

### Application Module (with steps)

```
modules/application/inventory_dashboard/
  inventory_dashboard.yaml
  app.py
  init_db.py
  inventory.service
  setup.sh
  finalize.sh
```

**inventory_dashboard.yaml**:

```yaml
id: inventory_dashboard
name: Python Inventory Dashboard
description: A Flask-based server inventory dashboard running as a systemd service on port 5001.
type: application
difficulty: easy
points: 0
category: web
tags: [web, flask, python, inventory]
conflicts: []
requires: []
steps:
  - run: setup.sh
  - copy: { src: app.py, dest: /opt/inventory/app.py }
  - copy: { src: inventory.service, dest: /etc/systemd/system/inventory.service }
  - copy: { src: init_db.py, dest: /tmp/init_db.py }
  - run: finalize.sh
verification:
  type: process_running
  process: "0.0.0.0:5001"
  expected: running
hints:
  - "Check what services are running on port 5001"
```

Application modules install infrastructure that vulnerability modules target. They award 0 points and auto-complete when the service is running. Vulnerability modules use `requires: [inventory_dashboard]` to depend on them — the selector automatically includes required modules and ensures their steps run first during the Docker build.

### Application Module (legacy script)

Simple modules can still use the `script` field:

```yaml
id: vulnerable_flask_app
name: Vulnerable Flask Application
type: application
difficulty: easy
points: 0
category: web
script: install_flask_app.sh
verification:
  type: process_running
  process: gunicorn
  expected: running
```

## Module Selection

The platform selects modules based on an event quota like:

```json
{
  "vulnerability": {"easy": 1, "medium": 1, "hard": 0},
  "hardening": {"easy": 1, "medium": 1, "hard": 0},
  "application": {"easy": 1},
  "categories": {"authentication": 2},
  "tags": {"privilege-escalation": 1}
}
```

The selector (`builder/selector.py`) runs three phases:
1. **Type/difficulty** — pick modules matching each type/difficulty slot
2. **Categories** (optional) — ensure at least N modules from a given category are selected (modules already picked in phase 1 count toward the total)
3. **Tags** (optional) — ensure at least N modules with a given tag are selected (same inclusive counting)

Across all phases, the selector will:
- Skip modules that conflict with already-selected modules (bidirectional)
- Auto-include any modules listed in `requires`
- Count dependency-pulled modules toward their type/difficulty quota

## Ansible Export Compatibility

Modules are automatically compatible with the Ansible export feature (`POST /admin/ansible-export`). The export generates an Ansible playbook that applies modules to bare machines using:

- `ansible.builtin.script` for `run` steps (executes the same `.sh` scripts)
- `ansible.builtin.copy` for `copy` steps (copies files to the same destination paths)

When writing module scripts, keep in mind they may run on bare machines (not just inside Docker builds). Scripts that rely on Docker-specific behavior (e.g., `COPY` creating parent directories) should explicitly create directories with `mkdir -p`.

## Tips

- Keep module IDs unique and descriptive in snake_case
- Use `conflicts` to prevent incompatible modules from being selected together (e.g. two modules that both modify `/etc/ssh/sshd_config`)
- Use `requires` for dependencies (e.g. a module that needs SSH to be misconfigured first)
- Shell scripts run as root during `docker build` — they should be idempotent
- Order hints from vague to specific so users can get progressive help
- `suggested_fix` is for admin/testing purposes — it is not shown to users during the challenge
