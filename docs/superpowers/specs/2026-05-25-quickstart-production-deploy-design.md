# Quickstart Production Deploy Script — Design

**Date:** 2026-05-25

## Problem

Deploying the full CTF-IT production stack today is a manual, error-prone sequence:
copy two separate `.env` templates, hand-generate a half-dozen secrets with
`openssl`/`htpasswd`, copy and fill the Caldera `local.yml`, then run
`docker compose up -d` from the right directory. A fresh server also needs Docker
installed first. There is no single entrypoint, so onboarding a new server is slow
and easy to get wrong (especially the `$$`-escaping of bcrypt hashes and the
working-directory nuance below).

## Solution

A single self-contained `quickstart.sh` at the repo root that bootstraps the full
production stack on a Linux server in one command. It installs Docker if missing,
generates all secrets and config files idempotently, and brings up
`deploy/docker-compose.yml`.

## Scope Decisions (settled)

- **Target:** production stack only (`deploy/docker-compose.yml`: Traefik + TLS,
  Dockhand, CTF API, Caldera, Semaphore + Postgres). Not the root single-container
  `docker-compose.yml`.
- **Docker:** install via the official `get.docker.com` convenience script if
  `docker` or the `docker compose` plugin is absent; otherwise skip.
- **Secrets:** auto-generate; prompt only for human-supplied values. Never
  overwrite an existing config file (idempotent).

## Critical Implementation Detail: Working Directory

The stack MUST be brought up from inside `deploy/`:

```bash
cd deploy && docker compose up -d --build
```

This is required because:
- Compose variable interpolation (`${DOMAIN}`, `${SEMAPHORE_*}`, etc.) reads `.env`
  from the project directory (cwd) → `deploy/.env`.
- The API service's `env_file: ../.env` resolves relative to the compose file →
  the root `.env`.
- The API `build.context: ..` resolves to the repo root.

Running from the repo root instead would make compose look for `deploy/.env`
variables in `./.env` and fail interpolation.

## Phases

1. **Preflight**
   - Require a Linux host and root (or working `sudo`).
   - `set -euo pipefail`. Resolve the script's own directory as `REPO_ROOT`.
   - Print a banner listing what will happen.

2. **Docker**
   - If `docker` and `docker compose` (plugin) both work, skip.
   - Else install via `curl -fsSL https://get.docker.com | sh`, enable the service,
     and re-verify. Fail clearly if still unavailable.

3. **Collect inputs** — prompt only for inputs needed to generate the config files
   that are still missing. If all three config files already exist, this phase is
   skipped entirely and the script goes straight to launch.
   - Prompt with defaults: `DOMAIN`, `ACME_EMAIL`, server public IP
     (→ `CALDERA_AGENT_URL=http://<IP>:8888`), Traefik dashboard admin username
     (default `admin`).
   - Optional: `VULTR_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_DOMAIN`.
   - `--non-interactive` reads every value from same-named environment variables
     and errors if a required one is unset.

4. **Generate root `./.env`** (skip if it exists)
   - From `.env.example`: `SECRET_KEY=$(openssl rand -hex 32)`, keep the example
     `EVENT_QUOTA` and `DATABASE_URL`, fill `VULTR_API_KEY` /
     `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_DOMAIN` if supplied.

5. **Generate `deploy/.env`** (skip if it exists)
   - `DOMAIN`, `ACME_EMAIL`, `CALDERA_AGENT_URL`.
   - `SEMAPHORE_ADMIN=admin`, `SEMAPHORE_ADMIN_PASSWORD`,
     `SEMAPHORE_ACCESS_KEY_ENCRYPTION`, `SEMAPHORE_POSTGRES_PASSWORD` — each
     `openssl rand -base64 32`.
   - `TRAEFIK_DASHBOARD_AUTH` and `REGISTRY_AUTH` — bcrypt basic-auth strings with
     every `$` doubled to `$$` for compose escaping (see Secret Generation).
   - `VULTR_API_KEY` mirrored here too (Semaphore container reads it).

6. **Generate `deploy/caldera/config/local.yml`** (skip if it exists)
   - Copy from `local.yml.example`, then replace every secret placeholder
     (`REPLACE_ME`, `REPLACE_WITH_KEY_FILE_PASSPHRASE`) with an independently
     generated `openssl rand -base64 32` value. The SSH-tunnel/FTP contact secrets
     get random values too — harmless, those contacts simply go unused.
     `REPLACE_WITH_KEY_FILE_PATH` is left as-is (the SSH tunnel C2 is not enabled).

7. **Launch**
   - `cd deploy && docker compose up -d --build`.

8. **Summary**
   - Print service URLs: `ctf.$DOMAIN`, `caldera.$DOMAIN`, `semaphore.$DOMAIN`,
     `dockhand.$DOMAIN`, `traefik.$DOMAIN`.
   - Print the generated Traefik dashboard password and the Semaphore admin
     username/password (the plaintext is shown here once; the env files remain the
     source of truth).
   - Next steps (manual, not automated): point DNS A-records for each subdomain at
     the server; optional per-user CTF base image build; optional Caldera plugin
     export via the admin UI.

## Secret Generation

| Value | Method |
|-------|--------|
| `SECRET_KEY` | `openssl rand -hex 32` |
| Semaphore admin / postgres / encryption | `openssl rand -base64 32` |
| Caldera `local.yml` secrets | `openssl rand -base64 32` (one per placeholder) |
| `TRAEFIK_DASHBOARD_AUTH`, `REGISTRY_AUTH` | bcrypt via `htpasswd` |

**htpasswd without a host dependency:** rather than requiring `apache2-utils`, run
it in a throwaway container (Docker is present by this phase):

```bash
hash=$(docker run --rm httpd:2.4-alpine htpasswd -nbB "$user" "$password")
escaped=$(printf '%s' "$hash" | sed 's/\$/\$\$/g')   # double $ for compose
```

Generated plaintext passwords (Traefik, Semaphore) are captured in shell variables
so they can be printed in the final summary.

## Idempotency & Re-runs

Each generated artifact (`./.env`, `deploy/.env`,
`deploy/caldera/config/local.yml`) is created only if absent; if present the script
logs `exists, skipping` and moves on. This makes re-running safe: secrets are never
silently rotated and a running stack is not disrupted. Regeneration requires a
`--force` flag, which backs up the existing file to `<file>.bak.<timestamp>` before
rewriting.

## Error Handling

- `set -euo pipefail` throughout.
- A `require_cmd <name>` helper for hard dependencies (`curl`, `openssl`, `sed`).
- Each phase prints a `==>` progress line; failures print `ERROR: <phase>: <reason>`
  to stderr and exit non-zero.
- Docker install failure, missing example templates, and compose-up failure each
  produce a specific message.

## Testing

This is a deploy script that installs Docker and brings up a live stack, so it
cannot run in the project's pytest/docker test harness. Verification is manual:

1. **Syntax/lint:** `bash -n quickstart.sh` and `shellcheck quickstart.sh` must pass.
2. **Dry idempotency:** with all three config files pre-created, the script reports
   `exists, skipping` for each and proceeds straight to compose-up.
3. **Fresh-server smoke test (manual, off this machine):** on a clean Ubuntu VPS,
   `./quickstart.sh` installs Docker, generates configs, and the six services reach
   healthy state; `ctf.$DOMAIN` serves the dashboard over TLS.

## Out of Scope

- DNS record creation (manual; the summary lists the required A-records).
- Per-user CTF base Docker image build (only needed for the Docker-challenge flow).
- Caldera plugin export/upload (done later via the admin UI).
- The root single-container `docker-compose.yml` dev path.
- Rotating or managing secrets after first deploy beyond the `--force` rewrite.
