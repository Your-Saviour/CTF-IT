# Node.js Notes API — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A Node.js 20 + Express REST API that stores notes in a SQLite database. Runs on port 3000 as a systemd service. Introduces 4 vulnerabilities covering SQL injection, world-readable database, hardcoded secrets, and debug mode.

| Field | Value |
|-------|-------|
| Port | 3000 |
| Runtime | Node.js 20 LTS + Express + better-sqlite3 |
| Path | `modules/application_external/notes_api/` |
| Service user | `notesapi` |
| Install dir | `/opt/notesapi/` |

---

## Directory Structure

```
modules/application_external/notes_api/
├── notes_api.yaml
├── setup.sh
├── finalize.sh
├── app.js
├── package.json
├── notesapi.service
└── vulns/
    ├── notes_sqli/
    │   ├── notes_sqli.yaml
    │   └── notes_sqli.sh
    ├── notes_world_readable_db/
    │   ├── notes_world_readable_db.yaml
    │   └── notes_world_readable_db.sh
    ├── notes_hardcoded_token/
    │   ├── notes_hardcoded_token.yaml
    │   └── notes_hardcoded_token.sh
    └── notes_debug_mode/
        ├── notes_debug_mode.yaml
        └── notes_debug_mode.sh
```

---

## Module Definitions

### Parent: `notes_api.yaml`

```yaml
id: notes_api
name: Node.js Notes API
description: A Node.js Express REST API for storing and searching notes, backed by a SQLite database. Runs on port 3000 as a systemd service.
type: application_external
difficulty: easy
points: 0
category: web
tags: [web, nodejs, express, sqlite, api]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: "0.0.0.0:3000"
  expected: running
hints:
  - "Check what services are listening on common web ports"
```

---

### Vuln 1: `notes_sqli.yaml`

```yaml
id: notes_sqli
name: SQL Injection in Notes Search
description: The /search endpoint builds SQL queries by concatenating user input directly, enabling classic SQL injection attacks.
type: vulnerability
difficulty: hard
points: 300
category: web
tags: [sqli, web, nodejs, injection]
conflicts: []
requires: [notes_api]
script: notes_sqli.sh
verification:
  type: file_not_contains
  path: /opt/notesapi/app.js
  pattern: "+ req.query"
suggested_fix: "Replace the raw string concatenation in /search with a parameterised query using better-sqlite3 prepared statements: db.prepare('SELECT * FROM notes WHERE body LIKE ?').all('%' + term + '%')"
hints:
  - "Review the /search endpoint for unsafe input handling"
  - "Look at how the search query is constructed in /opt/notesapi/app.js"
  - "Replace string concatenation with a prepared statement: db.prepare('SELECT * FROM notes WHERE body LIKE ?').all(term)"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1190
    name: "Exploit Public-Facing Application"
  recon:
    description: "Check if the /search endpoint is vulnerable to SQL injection"
    command: |
      curl -s "http://localhost:3000/search?q=' OR '1'='1" | grep -q "note" && echo "VULNERABLE: SQL injection in /search" || echo "SECURE: /search appears safe"
  exploit:
    description: "Dump all notes via SQL injection"
    command: |
      curl -s "http://localhost:3000/search?q=' OR '1'='1'--" | python3 -m json.tool
```

---

### Vuln 2: `notes_world_readable_db.yaml`

```yaml
id: notes_world_readable_db
name: World-Readable Notes Database
description: The SQLite database file is readable by any user on the system, exposing all stored notes and any sensitive data they contain.
type: vulnerability
difficulty: easy
points: 100
category: filesystem
tags: [permissions, filesystem, sqlite, data-exposure]
conflicts: []
requires: [notes_api]
script: notes_world_readable_db.sh
verification:
  type: file_permissions
  path: /opt/notesapi/notes.db
  expected: "640"
suggested_fix: "chmod 640 /opt/notesapi/notes.db && chown notesapi:notesapi /opt/notesapi/notes.db"
hints:
  - "Check file permissions on application data files"
  - "Look at the permissions on database files in /opt/notesapi/"
  - "Use chmod 640 to restrict the SQLite database to owner read/write and group read only"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if the notes database is world-readable"
    command: |
      stat -c "%a" /opt/notesapi/notes.db | grep -qE "^[0-9][0-9][4-7]$" && echo "VULNERABLE: notes.db is world-readable" || echo "SECURE: notes.db permissions are restricted"
  exploit:
    description: "Read the notes database as an unprivileged user"
    command: |
      sqlite3 /opt/notesapi/notes.db "SELECT * FROM notes;" 2>/dev/null && echo "Database contents read successfully"
```

---

### Vuln 3: `notes_hardcoded_token.yaml`

```yaml
id: notes_hardcoded_token
name: Hardcoded Admin API Token
description: The application source code contains a hardcoded admin API token used to authenticate privileged endpoints. Anyone who can read the source file obtains admin access.
type: vulnerability
difficulty: medium
points: 200
category: authentication
tags: [hardcoded-credentials, authentication, nodejs]
conflicts: []
requires: [notes_api]
script: notes_hardcoded_token.sh
verification:
  type: file_not_contains
  path: /opt/notesapi/app.js
  pattern: "SuperSecret123"
suggested_fix: "Move the admin token to an environment variable (e.g. ADMIN_TOKEN) set in the systemd service file under [Service] Environment=, and reference it in app.js as process.env.ADMIN_TOKEN"
hints:
  - "Review the application source code for embedded secrets"
  - "Search /opt/notesapi/app.js for hardcoded credential strings"
  - "Move the token to an environment variable in the systemd unit file and remove it from app.js"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1552.001
    name: "Unsecured Credentials: Credentials In Files"
  recon:
    description: "Check if app.js contains a hardcoded admin token"
    command: |
      grep -q "SuperSecret123" /opt/notesapi/app.js && echo "VULNERABLE: hardcoded admin token found" || echo "SECURE: no hardcoded token detected"
  exploit:
    description: "Extract the admin token and access the privileged endpoint"
    command: |
      TOKEN=$(grep -oP "SuperSecret123[^'\"]*" /opt/notesapi/app.js | head -1)
      curl -s -H "X-Admin-Token: $TOKEN" http://localhost:3000/admin/notes | python3 -m json.tool
```

---

### Vuln 4: `notes_debug_mode.yaml`

```yaml
id: notes_debug_mode
name: Node.js Debug Mode Enabled
description: The systemd service unit file sets DEBUG=true, causing the application to output verbose debug information including internal state and request details.
type: vulnerability
difficulty: easy
points: 100
category: web
tags: [misconfiguration, nodejs, debug, information-disclosure]
conflicts: []
requires: [notes_api]
script: notes_debug_mode.sh
verification:
  type: file_not_contains
  path: /etc/systemd/system/notesapi.service
  pattern: "DEBUG=true"
suggested_fix: "Edit /etc/systemd/system/notesapi.service and remove the DEBUG=true line from [Service] Environment=, then run: systemctl daemon-reload && systemctl restart notesapi"
hints:
  - "Check the systemd service unit configuration for the notes API"
  - "Look at environment variables set in /etc/systemd/system/notesapi.service"
  - "Remove the DEBUG=true environment variable and reload the service with systemctl daemon-reload && systemctl restart notesapi"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if debug mode is enabled in the notesapi service unit"
    command: |
      grep -q "DEBUG=true" /etc/systemd/system/notesapi.service && echo "VULNERABLE: debug mode enabled" || echo "SECURE: debug mode not enabled"
  exploit:
    description: "Trigger verbose debug output to extract internal application state"
    command: |
      curl -s http://localhost:3000/debug 2>/dev/null || curl -v http://localhost:3000/notes 2>&1 | head -50
```

---

## Setup Scripts

### `setup.sh`

1. Install Node.js 20 LTS via NodeSource (`curl -fsSL https://deb.nodesource.com/setup_20.x | bash -` then `apt-get install -y nodejs`) — idempotent, safe to run if already installed.
2. Create `/opt/notesapi/` directory.
3. Copy `app.js`, `package.json` to `/opt/notesapi/`.
4. Run `npm install --omit=dev` in `/opt/notesapi/` to install `express` and `better-sqlite3`.
5. Copy `notesapi.service` to `/etc/systemd/system/notesapi.service`.

### `finalize.sh`

1. Create `notesapi` system user if it doesn't exist (`useradd -r -s /bin/false notesapi`).
2. Set ownership: `chown -R notesapi:notesapi /opt/notesapi/`.
3. Set permissions: `chmod 750 /opt/notesapi/ && chmod 640 /opt/notesapi/notes.db` (notes.db will be created on first run).
4. `systemctl daemon-reload && systemctl enable --now notesapi`.

---

## Application Source (`app.js`)

A ~100-line Express REST API with these endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/notes` | Return all notes as JSON array |
| `POST` | `/notes` | Create a note (`{title, body}`) |
| `GET` | `/search?q=<term>` | Search notes by body — **vulnerable to SQLi in vuln variant** |
| `POST` | `/login` | Accepts `{username, password}`, returns `{token}` |
| `GET` | `/admin/notes` | Admin-only endpoint, requires `X-Admin-Token` header |
| `GET` | `/debug` | (only active when `DEBUG=true`) Dumps app config |

**Secure baseline** (what `setup.sh` installs): Uses prepared statements in `/search`, no hardcoded tokens, DEBUG not set, db permissions 640.

**Vuln scripts** apply targeted patches: `notes_sqli.sh` replaces the prepared statement with concatenation; `notes_hardcoded_token.sh` inserts the literal token string; `notes_debug_mode.sh` adds `Environment=DEBUG=true` to the service unit; `notes_world_readable_db.sh` runs `chmod 644 /opt/notesapi/notes.db`.

---

## `package.json`

```json
{
  "name": "notesapi",
  "version": "1.0.0",
  "description": "Simple notes REST API",
  "main": "app.js",
  "scripts": {
    "start": "node app.js"
  },
  "dependencies": {
    "express": "^4.18.2",
    "better-sqlite3": "^9.4.3"
  }
}
```

---

## `notesapi.service`

```ini
[Unit]
Description=Notes API
After=network.target

[Service]
Type=simple
User=notesapi
WorkingDirectory=/opt/notesapi
ExecStart=/usr/bin/node /opt/notesapi/app.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Verification Checklist

For each module, test on a fresh VM:

1. **notes_api**: Run `setup.sh` + `finalize.sh` → `curl http://localhost:3000/notes` → expect JSON array response. `systemctl is-active notesapi` → `active`.
2. **notes_sqli**: Run `notes_sqli.sh` → `curl "http://localhost:3000/search?q=' OR '1'='1"` → returns all notes. Fix: update app.js → retest, now returns empty or normal results only.
3. **notes_world_readable_db**: Run script → `stat -c "%a" /opt/notesapi/notes.db` → `644`. Fix: `chmod 640` → retest → `640`.
4. **notes_hardcoded_token**: Run script → `grep SuperSecret123 /opt/notesapi/app.js` → found. Fix: move to env var → grep returns nothing.
5. **notes_debug_mode**: Run script → `grep DEBUG=true /etc/systemd/system/notesapi.service` → found. Fix: remove line, reload → grep returns nothing.

---

## Port / Conflict Notes

- Port 3000 is not used by any existing module.
- Node.js 20 installation is idempotent — safe if `nextjs_portal` is also selected.
- No file path conflicts with any existing module.
