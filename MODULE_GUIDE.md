# Module Guide

How to create new vulnerability and hardening modules for CTF-IT.

## Folder Structure

Each module lives in its own folder under `modules/vulns/` or `modules/hardening/`:

```
modules/
  vulns/<module_id>/
    <module_id>.yaml        # Required: module definition
    <module_id>.sh          # Optional: shell script to introduce the vulnerability
  hardening/<module_id>/
    <module_id>.yaml        # Required: module definition
```

Vulnerability modules typically include a shell script that introduces a misconfiguration during the Docker build. Hardening modules usually don't have a script — the user is expected to implement the fix from scratch.

## YAML Reference

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique snake_case identifier. Must match the folder name. |
| `name` | string | Human-readable display name. |
| `description` | string | What the issue is and why it matters. Shown to users as their task. |
| `type` | string | `vulnerability` or `hardening` |
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
| `script` | string | `null` | Filename of the shell script (vulnerability modules only). |
| `hints` | list[string] | `[]` | Progressive hints shown to users. Order from vague to specific. |
| `suggested_fix` | string | `null` | The command(s) that fix the issue. Used for admin reference/testing. |

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

## Module Selection

The platform selects modules based on an event quota like:

```json
{
  "vulnerability": {"easy": 1, "medium": 0, "hard": 0},
  "hardening": {"easy": 0, "medium": 1, "hard": 0}
}
```

The selector (`builder/selector.py`) will:
1. Pick modules matching each type/difficulty slot
2. Skip modules that conflict with already-selected modules
3. Auto-include any modules listed in `requires`

## Tips

- Keep module IDs unique and descriptive in snake_case
- Use `conflicts` to prevent incompatible modules from being selected together (e.g. two modules that both modify `/etc/ssh/sshd_config`)
- Use `requires` for dependencies (e.g. a module that needs SSH to be misconfigured first)
- Shell scripts run as root during `docker build` — they should be idempotent
- Order hints from vague to specific so users can get progressive help
- `suggested_fix` is for admin/testing purposes — it is not shown to users during the challenge
