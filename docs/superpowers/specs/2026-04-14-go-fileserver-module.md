# Go File Server — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A pre-compiled Go binary that serves files over HTTPS on port 8000. The binary is built ahead of time from vendored source and copied to the target VM — no Go toolchain is installed on the target. Introduces 5 vulnerabilities covering path traversal, world-readable TLS private key, hidden file exposure, anonymous upload, and running as root.

| Field | Value |
|-------|-------|
| Port | 8000 |
| Runtime | Pre-compiled Go binary (linux/amd64 + linux/arm64) |
| Path | `modules/application_external/go_fileserver/` |
| Service user | `fileserver` |
| Install dir | `/opt/fileserver/` |
| Served root | `/opt/fileserver/files/` |

---

## Directory Structure

```
modules/application_external/go_fileserver/
├── go_fileserver.yaml
├── setup.sh
├── finalize.sh
├── config.toml          # default (secure) config
├── server.crt           # self-signed TLS cert
├── server.key           # TLS private key
├── fileserver.service
├── Makefile             # cross-compile both architectures
├── src/
│   ├── main.go
│   └── go.mod
├── bin/
│   ├── fileserver-linux-amd64   # pre-compiled
│   └── fileserver-linux-arm64   # pre-compiled
└── vulns/
    ├── fileserver_path_traversal/
    │   ├── fileserver_path_traversal.yaml
    │   └── fileserver_path_traversal.sh
    ├── fileserver_default_tls_key/
    │   ├── fileserver_default_tls_key.yaml
    │   └── fileserver_default_tls_key.sh
    ├── fileserver_hidden_files/
    │   ├── fileserver_hidden_files.yaml
    │   └── fileserver_hidden_files.sh
    ├── fileserver_anon_upload/
    │   ├── fileserver_anon_upload.yaml
    │   └── fileserver_anon_upload.sh
    └── fileserver_running_as_root/
        ├── fileserver_running_as_root.yaml
        └── fileserver_running_as_root.sh
```

---

## Module Definitions

### Parent: `go_fileserver.yaml`

```yaml
id: go_fileserver
name: Go File Server
description: A lightweight HTTPS file server written in Go. Serves files from /opt/fileserver/files/ on port 8000. Configured via config.toml and run as a systemd service.
type: application_external
difficulty: medium
points: 0
category: web
tags: [web, go, fileserver, https]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: "0.0.0.0:8000"
  expected: running
hints:
  - "Check what services are listening on common web ports"
```

---

### Vuln 1: `fileserver_path_traversal.yaml`

```yaml
id: fileserver_path_traversal
name: Path Traversal in File Server
description: The file server does not sanitise path components, allowing requests like /files/../../../../etc/passwd to read files outside the intended serve root.
type: vulnerability
difficulty: hard
points: 300
category: web
tags: [path-traversal, web, go, lfi]
conflicts: []
requires: [go_fileserver]
script: fileserver_path_traversal.sh
verification:
  type: file_contains
  path: /opt/fileserver/config.toml
  pattern: "sanitize_paths = true"
suggested_fix: "In /opt/fileserver/config.toml, set sanitize_paths = true and restart the service: systemctl restart fileserver"
hints:
  - "Review the file server configuration for path handling settings"
  - "Look at the sanitize_paths setting in /opt/fileserver/config.toml"
  - "Set sanitize_paths = true in config.toml and restart the fileserver service"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if path traversal is possible on the file server"
    command: |
      grep -q "sanitize_paths = false" /opt/fileserver/config.toml && echo "VULNERABLE: path traversal enabled (sanitize_paths = false)" || echo "SECURE: path sanitisation is active"
  exploit:
    description: "Read /etc/passwd via path traversal"
    command: |
      curl -sk "https://localhost:8000/files/../../../../etc/passwd" | head -5
```

---

### Vuln 2: `fileserver_default_tls_key.yaml`

```yaml
id: fileserver_default_tls_key
name: World-Readable TLS Private Key
description: The TLS private key file server.key is readable by all users on the system. An attacker who can read it can decrypt captured HTTPS traffic or impersonate the server.
type: vulnerability
difficulty: easy
points: 100
category: filesystem
tags: [tls, permissions, filesystem, cryptography]
conflicts: []
requires: [go_fileserver]
script: fileserver_default_tls_key.sh
verification:
  type: file_permissions
  path: /opt/fileserver/server.key
  expected: "600"
suggested_fix: "chmod 600 /opt/fileserver/server.key && chown fileserver:fileserver /opt/fileserver/server.key"
hints:
  - "Check permissions on TLS certificate and key files"
  - "Look at who can read /opt/fileserver/server.key"
  - "Use chmod 600 to restrict the private key to the owner only: chmod 600 /opt/fileserver/server.key"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1552.004
    name: "Unsecured Credentials: Private Keys"
  recon:
    description: "Check if the TLS private key is world-readable"
    command: |
      stat -c "%a" /opt/fileserver/server.key | grep -qE "^[0-9][0-9][4-7]$" && echo "VULNERABLE: server.key is world-readable" || echo "SECURE: server.key permissions are restricted"
  exploit:
    description: "Read the TLS private key as an unprivileged user"
    command: |
      cat /opt/fileserver/server.key | head -5
      echo "Private key exfiltrated — can now decrypt captured HTTPS sessions"
```

---

### Vuln 3: `fileserver_hidden_files.yaml`

```yaml
id: fileserver_hidden_files
name: Directory Listing Exposes Hidden Files
description: The file server is configured with show_hidden = true, causing dotfiles and hidden directories (e.g. .git, .env, .ssh) to appear in directory listings and be downloadable.
type: vulnerability
difficulty: medium
points: 200
category: web
tags: [information-disclosure, web, go, configuration]
conflicts: []
requires: [go_fileserver]
script: fileserver_hidden_files.sh
verification:
  type: file_not_contains
  path: /opt/fileserver/config.toml
  pattern: "show_hidden = true"
suggested_fix: "In /opt/fileserver/config.toml, remove or set show_hidden = false, then restart the service: systemctl restart fileserver"
hints:
  - "Review the file server configuration for directory listing options"
  - "Look for hidden file exposure settings in /opt/fileserver/config.toml"
  - "Set show_hidden = false in config.toml and restart the fileserver service"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if hidden files are exposed in directory listings"
    command: |
      grep -q "show_hidden = true" /opt/fileserver/config.toml && echo "VULNERABLE: hidden files exposed" || echo "SECURE: hidden files not shown"
  exploit:
    description: "Browse directory listing for hidden files"
    command: |
      curl -sk "https://localhost:8000/files/" | grep -oP 'href="[^"]+"' | grep "\."
```

---

### Vuln 4: `fileserver_anon_upload.yaml`

```yaml
id: fileserver_anon_upload
name: Anonymous Upload Enabled
description: The file server allows unauthenticated HTTP PUT requests to upload arbitrary files into the served directory, enabling remote code staging or data injection.
type: vulnerability
difficulty: medium
points: 200
category: web
tags: [misconfiguration, web, go, file-upload, unauthenticated]
conflicts: []
requires: [go_fileserver]
script: fileserver_anon_upload.sh
verification:
  type: file_not_contains
  path: /opt/fileserver/config.toml
  pattern: "allow_anonymous_upload = true"
suggested_fix: "In /opt/fileserver/config.toml, set allow_anonymous_upload = false and restart the service: systemctl restart fileserver"
hints:
  - "Review the file server configuration for upload permissions"
  - "Look for upload-related settings in /opt/fileserver/config.toml"
  - "Set allow_anonymous_upload = false in config.toml and restart the fileserver service"
caldera:
  tactic: persistence
  technique:
    attack_id: T1505
    name: "Server Software Component"
  recon:
    description: "Check if anonymous upload is enabled in the file server config"
    command: |
      grep -q "allow_anonymous_upload = true" /opt/fileserver/config.toml && echo "VULNERABLE: anonymous upload enabled" || echo "SECURE: anonymous upload disabled"
  exploit:
    description: "Upload a file to the server without authentication"
    command: |
      echo '#!/bin/bash\nid' > /tmp/backdoor.sh
      curl -sk -X PUT "https://localhost:8000/files/backdoor.sh" --data-binary @/tmp/backdoor.sh
      curl -sk "https://localhost:8000/files/" | grep backdoor
```

---

### Vuln 5: `fileserver_running_as_root.yaml`

```yaml
id: fileserver_running_as_root
name: File Server Running as Root
description: The systemd service unit does not specify a User directive, causing the file server process to run as root. Any path traversal or upload vulnerability becomes an immediate full system compromise.
type: vulnerability
difficulty: medium
points: 200
category: services
tags: [privilege-escalation, services, misconfiguration, systemd]
conflicts: []
requires: [go_fileserver]
script: fileserver_running_as_root.sh
verification:
  type: file_contains
  path: /etc/systemd/system/fileserver.service
  pattern: "User=fileserver"
suggested_fix: "Edit /etc/systemd/system/fileserver.service and ensure the [Service] section contains 'User=fileserver', then run: systemctl daemon-reload && systemctl restart fileserver"
hints:
  - "Check what user the file server process is running as"
  - "Look at the [Service] section of /etc/systemd/system/fileserver.service"
  - "Add 'User=fileserver' under [Service] in the unit file, then reload and restart: systemctl daemon-reload && systemctl restart fileserver"
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548
    name: "Abuse Elevation Control Mechanism"
  recon:
    description: "Check if the file server is running as root"
    command: |
      ps aux | grep fileserver | grep -v grep | grep -q "^root" && echo "VULNERABLE: fileserver running as root" || echo "SECURE: fileserver not running as root"
  exploit:
    description: "Confirm root execution and chain with path traversal"
    command: |
      ps aux | grep fileserver | grep -v grep | awk '{print "Running as: " $1}'
      curl -sk "https://localhost:8000/files/../../../../root/.ssh/id_rsa" 2>/dev/null | head -5
```

---

## Setup Scripts

### `setup.sh`

1. Detect architecture: `ARCH=$(uname -m)` → `amd64` or `arm64`.
2. Create `fileserver` system user: `useradd -r -s /bin/false fileserver`
3. Create `/opt/fileserver/` and `/opt/fileserver/files/`.
4. Copy architecture-specific binary from `bin/fileserver-linux-${ARCH}` to `/opt/fileserver/fileserver`.
5. `chmod 755 /opt/fileserver/fileserver`
6. Copy `config.toml`, `server.crt`, `server.key` to `/opt/fileserver/`.
7. Create a few sample files in `/opt/fileserver/files/` (e.g. `readme.txt`, `sample.csv`).
8. Copy `fileserver.service` to `/etc/systemd/system/fileserver.service`.

### `finalize.sh`

1. `chown -R fileserver:fileserver /opt/fileserver/`
2. `chmod 600 /opt/fileserver/server.key`
3. `chmod 644 /opt/fileserver/server.crt`
4. `chmod 640 /opt/fileserver/config.toml`
5. `systemctl daemon-reload && systemctl enable --now fileserver`

---

## Application Source (`src/main.go`)

~150-line Go program:

- Reads `config.toml` at startup via a simple TOML parser (use `github.com/BurntSushi/toml` or parse manually)
- Serves files from the configured root under `/files/`
- `sanitize_paths` option: when true, uses `filepath.Clean` and checks the resolved path is within the serve root
- `show_hidden` option: filters dotfiles from directory listings when false
- `allow_anonymous_upload` option: enables HTTP PUT handler when true
- Generates a self-signed cert at build time (or use the bundled `server.crt`/`server.key`)
- Listens on `:8000` with TLS

**Build (Makefile):**

```makefile
build:
	GOOS=linux GOARCH=amd64 go build -o bin/fileserver-linux-amd64 ./src/
	GOOS=linux GOARCH=arm64 go build -o bin/fileserver-linux-arm64 ./src/
```

---

## Default `config.toml`

```toml
# Go File Server configuration
serve_root = "/opt/fileserver/files"
listen = "0.0.0.0:8000"
tls_cert = "/opt/fileserver/server.crt"
tls_key = "/opt/fileserver/server.key"

# Security options (secure defaults)
sanitize_paths = true
show_hidden = false
allow_anonymous_upload = false
```

---

## `fileserver.service`

```ini
[Unit]
Description=Go File Server
After=network.target

[Service]
Type=simple
User=fileserver
WorkingDirectory=/opt/fileserver
ExecStart=/opt/fileserver/fileserver
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Vuln Scripts

| Script | What it does |
|--------|-------------|
| `fileserver_path_traversal.sh` | Replaces `sanitize_paths = true` with `sanitize_paths = false` in `config.toml`, restarts service |
| `fileserver_default_tls_key.sh` | `chmod 644 /opt/fileserver/server.key` |
| `fileserver_hidden_files.sh` | Replaces `show_hidden = false` with `show_hidden = true` in `config.toml`, restarts service |
| `fileserver_anon_upload.sh` | Replaces `allow_anonymous_upload = false` with `allow_anonymous_upload = true` in `config.toml`, restarts service |
| `fileserver_running_as_root.sh` | Removes `User=fileserver` from `fileserver.service`, runs `systemctl daemon-reload && systemctl restart fileserver` |

---

## Verification Checklist

1. **go_fileserver**: Run `setup.sh` + `finalize.sh` → `curl -sk https://localhost:8000/files/` → returns file listing. `ss -tlnp | grep 8000` → listening.
2. **fileserver_path_traversal**: Run script → `curl -sk "https://localhost:8000/files/../../../../etc/passwd"` → returns passwd file. Fix: set `sanitize_paths = true` → retest → 403/404.
3. **fileserver_default_tls_key**: Run script → `stat -c "%a" /opt/fileserver/server.key` → 644. Fix: `chmod 600` → 600.
4. **fileserver_hidden_files**: Run script → create a `.secret` file in files dir, `curl -sk https://localhost:8000/files/` → shows `.secret`. Fix: set `show_hidden = false` → hidden files not listed.
5. **fileserver_anon_upload**: Run script → `curl -sk -X PUT https://localhost:8000/files/test.txt -d "hello"` → succeeds. Fix: set `allow_anonymous_upload = false` → 403.
6. **fileserver_running_as_root**: Run script → `ps aux | grep fileserver | head -2` → `root`. Fix: add `User=fileserver`, reload → `fileserver` user.

---

## Port / Conflict Notes

- Port 8000 is not used by any existing module.
- No Go toolchain installed on the target — only the pre-compiled binary is copied.
- The `src/` directory and `Makefile` exist only in the module repo; they are not copied to the target VM.
- Pre-compiled binaries must be committed to the module directory (or generated by CI before deployment).
- No file path conflicts with existing modules.
