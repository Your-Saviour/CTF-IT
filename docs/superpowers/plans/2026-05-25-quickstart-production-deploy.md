# Quickstart Production Deploy Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `quickstart.sh` at the repo root that bootstraps the full CTF-IT production stack (`deploy/docker-compose.yml`) on a Linux server in one command — installing Docker if missing, generating all secrets/config idempotently, and bringing the stack up.

**Architecture:** A single Bash script composed of small phase functions (`phase_preflight`, `phase_docker`, `collect_inputs`, `gen_root_env`, `gen_deploy_env`, `gen_caldera_config`, `launch_stack`, `print_summary`) driven by `main`. The script is sourceable — a `BASH_SOURCE`/`$0` guard runs `main` only on direct execution — so each generator function can be unit-verified against a temp dir without side effects. The stack is launched from inside `deploy/` so compose interpolation reads `deploy/.env` while the API's `env_file: ../.env` resolves to the root `.env`.

**Tech Stack:** Bash, openssl (secret generation), Docker (`get.docker.com` installer; `httpd:2.4-alpine` for `htpasswd`), docker compose v2.

---

## File Map

**Create:**
- `quickstart.sh` — the entire deploy script (repo root). Single responsibility: bootstrap the production stack.

**Reads at runtime (not modified):**
- `.env.example`, `deploy/.env.example`, `deploy/caldera/config/local.yml.example` — templates.
- `deploy/docker-compose.yml` — the stack definition.

No existing files are modified. There is no pytest integration (this script installs Docker and launches a live stack); verification is `bash -n`, `shellcheck`, and sourcing individual functions against temp dirs.

---

### Task 1: Script skeleton, helpers, arg parsing, stubs

**Files:**
- Create: `quickstart.sh`

- [ ] **Step 1: Create the skeleton with helpers, globals, arg parsing, stub phases, and the source guard**

Create `quickstart.sh`:

```bash
#!/usr/bin/env bash
#
# quickstart.sh — bootstrap the full CTF-IT production stack.
#
# Installs Docker if missing, generates secrets and env/config files
# idempotently, and brings up deploy/docker-compose.yml.
#
# Usage: ./quickstart.sh [--non-interactive] [--force] [--help]
#
set -euo pipefail

# ── Globals ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ROOT_ENV="$REPO_ROOT/.env"
DEPLOY_ENV="$REPO_ROOT/deploy/.env"
CALDERA_CFG="$REPO_ROOT/deploy/caldera/config/local.yml"
CALDERA_CFG_EXAMPLE="$REPO_ROOT/deploy/caldera/config/local.yml.example"

NON_INTERACTIVE=false
FORCE=false
SUDO=""

# Inputs (may be pre-set via environment in --non-interactive mode)
DOMAIN="${DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"
SERVER_IP="${SERVER_IP:-}"
TRAEFIK_USER="${TRAEFIK_USER:-}"
VULTR_API_KEY="${VULTR_API_KEY:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CLOUDFLARE_DOMAIN="${CLOUDFLARE_DOMAIN:-}"

# Generated plaintext (printed in summary)
TRAEFIK_PASSWORD=""
SEMAPHORE_ADMIN_PASSWORD=""

# ── Logging helpers ──────────────────────────────────────────────────────────
log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
err()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || err "Required command not found: $1"; }

# Double every '$' so docker-compose does not interpolate bcrypt hashes.
escape_for_compose() { printf '%s' "$1" | sed 's/\$/\$\$/g'; }

usage() {
  cat <<'EOF'
Usage: ./quickstart.sh [options]

Bootstraps the full CTF-IT production stack (deploy/docker-compose.yml):
installs Docker if missing, generates secrets and env files, brings up the stack.

Options:
  --non-interactive   Read all inputs from environment variables; never prompt.
  --force             Regenerate env/config files even if present (backs up first).
  -h, --help          Show this help and exit.

Non-interactive env vars: DOMAIN, ACME_EMAIL, SERVER_IP, TRAEFIK_USER,
  VULTR_API_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_DOMAIN.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --non-interactive) NON_INTERACTIVE=true ;;
      --force) FORCE=true ;;
      -h|--help) usage; exit 0 ;;
      *) err "Unknown option: $1 (try --help)" ;;
    esac
    shift
  done
}

# ── Phase stubs (replaced in later tasks) ────────────────────────────────────
phase_preflight()   { log "STUB phase_preflight"; }
phase_docker()      { log "STUB phase_docker"; }
collect_inputs()    { log "STUB collect_inputs"; }
gen_root_env()      { log "STUB gen_root_env"; }
gen_deploy_env()    { log "STUB gen_deploy_env"; }
gen_caldera_config(){ log "STUB gen_caldera_config"; }
launch_stack()      { log "STUB launch_stack"; }
print_summary()     { log "STUB print_summary"; }

main() {
  parse_args "$@"
  phase_preflight
  phase_docker
  collect_inputs
  gen_root_env
  gen_deploy_env
  gen_caldera_config
  launch_stack
  print_summary
}

# Run main only on direct execution (sourceable for testing).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x quickstart.sh`

- [ ] **Step 3: Verify syntax and help output**

Run: `bash -n quickstart.sh && ./quickstart.sh --help`
Expected: no syntax errors; usage text prints; exit 0.

- [ ] **Step 4: Verify shellcheck is clean (if available)**

Run: `command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck not installed — skipping"`
Expected: no warnings, or the skip message.

- [ ] **Step 5: Verify the source guard works (functions load, main does not run)**

Run: `bash -c 'source ./quickstart.sh; type escape_for_compose >/dev/null && echo SOURCED_OK'`
Expected: prints `SOURCED_OK` and nothing else (no STUB lines — main did not run).

- [ ] **Step 6: Verify escape_for_compose doubles dollar signs**

Run: `bash -c 'source ./quickstart.sh; escape_for_compose "admin:$2y$05$abc"'`
Expected: `admin:$$2y$$05$$abc`

- [ ] **Step 7: Commit**

```bash
git add quickstart.sh
git commit -m "feat: add quickstart.sh skeleton with helpers and phase stubs"
```

---

### Task 2: Preflight and Docker install phases

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Replace the `phase_preflight` stub with the real implementation**

Replace this line in `quickstart.sh`:

```bash
phase_preflight()   { log "STUB phase_preflight"; }
```

with:

```bash
phase_preflight() {
  log "Preflight checks"
  [[ "$(uname -s)" == "Linux" ]] || err "This script must run on Linux (got $(uname -s))."
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || err "Not running as root and 'sudo' not found."
    SUDO="sudo"
  fi
  require_cmd curl
  require_cmd openssl
  require_cmd sed
  require_cmd awk
  [[ -f "$REPO_ROOT/.env.example" ]] || err "Missing $REPO_ROOT/.env.example"
  [[ -f "$REPO_ROOT/deploy/.env.example" ]] || err "Missing $REPO_ROOT/deploy/.env.example"
  [[ -f "$CALDERA_CFG_EXAMPLE" ]] || err "Missing $CALDERA_CFG_EXAMPLE"
}
```

- [ ] **Step 2: Replace the `phase_docker` stub with the real implementation**

Replace this line:

```bash
phase_docker()      { log "STUB phase_docker"; }
```

with:

```bash
phase_docker() {
  log "Checking Docker"
  if command -v docker >/dev/null 2>&1 && $SUDO docker compose version >/dev/null 2>&1; then
    log "Docker and compose plugin present — skipping install"
    return
  fi
  log "Installing Docker via get.docker.com"
  curl -fsSL https://get.docker.com | $SUDO sh || err "Docker installation failed."
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  command -v docker >/dev/null 2>&1 && $SUDO docker compose version >/dev/null 2>&1 \
    || err "Docker still unavailable after install."
}
```

- [ ] **Step 3: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 4: Verify preflight passes on a healthy checkout**

Run: `bash -c 'source ./quickstart.sh; phase_preflight && echo PREFLIGHT_OK'`
Expected: prints `==> Preflight checks` then `PREFLIGHT_OK` (assumes you are on Linux with curl/openssl/sed/awk; on macOS this will correctly error on the Linux check — run on the target server or skip).

- [ ] **Step 5: Verify docker detection skips when Docker is present**

Run: `bash -c 'source ./quickstart.sh; phase_docker'`
Expected (Docker already installed): `==> Checking Docker` then `==> Docker and compose plugin present — skipping install`.

- [ ] **Step 6: Commit**

```bash
git add quickstart.sh
git commit -m "feat: implement preflight checks and Docker install phase"
```

---

### Task 3: Input collection (prompts + non-interactive)

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Add the `prompt_var` and `backup_if_exists` helpers**

In `quickstart.sh`, immediately after the `escape_for_compose` function, add:

```bash
# Prompt for a variable unless already set. In --non-interactive mode use the
# default (erroring if a required value has no default).
prompt_var() {
  local name="$1" msg="$2" def="${3:-}" required="${4:-false}" current val
  current="${!name:-}"
  [[ -n "$current" ]] && return
  if [[ "$NON_INTERACTIVE" == true ]]; then
    if [[ -z "$def" && "$required" == true ]]; then
      err "Required value '$name' not set (non-interactive mode)."
    fi
    printf -v "$name" '%s' "$def"
    return
  fi
  read -rp "$msg${def:+ [$def]}: " val
  printf -v "$name" '%s' "${val:-$def}"
}

# When --force and the file exists, copy it to a timestamped backup.
backup_if_exists() {
  local f="$1"
  if [[ -f "$f" && "$FORCE" == true ]]; then
    local bak="$f.bak.$(date +%Y%m%d%H%M%S)"
    cp "$f" "$bak"
    log "Backed up existing $f -> $bak"
  fi
}
```

- [ ] **Step 2: Replace the `collect_inputs` stub with the real implementation**

Replace this line:

```bash
collect_inputs()    { log "STUB collect_inputs"; }
```

with:

```bash
collect_inputs() {
  local need_deploy=false need_root=false
  { [[ -f "$DEPLOY_ENV" ]] && [[ "$FORCE" != true ]]; } || need_deploy=true
  { [[ -f "$ROOT_ENV" ]]   && [[ "$FORCE" != true ]]; } || need_root=true

  if [[ "$need_deploy" == false && "$need_root" == false ]]; then
    log "All env files present — skipping input collection"
    return
  fi

  log "Collecting configuration"
  if [[ "$need_deploy" == true ]]; then
    prompt_var DOMAIN "Base domain (e.g. example.com)" "" true
    prompt_var ACME_EMAIL "Email for Let's Encrypt certificates" "" true
    prompt_var SERVER_IP "Server public IP (for Caldera agent callback)" "" true
    prompt_var TRAEFIK_USER "Traefik dashboard admin username" "admin" false
  fi
  # Optional — relevant to both root and deploy env files.
  prompt_var VULTR_API_KEY "Vultr API key (optional, blank to skip)" "" false
  prompt_var CLOUDFLARE_API_TOKEN "Cloudflare API token (optional, blank to skip)" "" false
  prompt_var CLOUDFLARE_DOMAIN "Cloudflare domain (optional, blank to skip)" "" false
}
```

- [ ] **Step 3: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 4: Verify non-interactive mode reads env vars and defaults TRAEFIK_USER**

Run:
```bash
bash -c '
  source ./quickstart.sh
  NON_INTERACTIVE=true FORCE=true
  DEPLOY_ENV=/nonexistent/deploy.env ROOT_ENV=/nonexistent/root.env
  DOMAIN=ex.com ACME_EMAIL=a@b.c SERVER_IP=1.2.3.4
  collect_inputs
  echo "DOMAIN=$DOMAIN USER=$TRAEFIK_USER"
'
```
Expected: `DOMAIN=ex.com USER=admin`

- [ ] **Step 5: Verify non-interactive mode errors on a missing required value**

Run:
```bash
bash -c '
  source ./quickstart.sh
  NON_INTERACTIVE=true FORCE=true
  DEPLOY_ENV=/nonexistent/deploy.env ROOT_ENV=/nonexistent/root.env
  collect_inputs
' ; echo "exit=$?"
```
Expected: `ERROR: Required value 'DOMAIN' not set (non-interactive mode).` and `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add quickstart.sh
git commit -m "feat: add input collection with interactive and non-interactive modes"
```

---

### Task 4: Generate the root `.env`

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Replace the `gen_root_env` stub with the real implementation**

Replace this line:

```bash
gen_root_env()      { log "STUB gen_root_env"; }
```

with:

```bash
gen_root_env() {
  if [[ -f "$ROOT_ENV" && "$FORCE" != true ]]; then
    log "$ROOT_ENV exists — skipping"
    return
  fi
  backup_if_exists "$ROOT_ENV"
  local secret_key quota
  secret_key="$(openssl rand -hex 32)"
  quota="$(grep -E '^EVENT_QUOTA=' "$REPO_ROOT/.env.example" | head -n1 | cut -d= -f2-)"
  {
    echo "SECRET_KEY=$secret_key"
    echo "DATABASE_URL=sqlite:///data/ctf.db"
    echo "EVENT_QUOTA=$quota"
    [[ -n "$VULTR_API_KEY" ]]        && echo "VULTR_API_KEY=$VULTR_API_KEY"
    [[ -n "$CLOUDFLARE_API_TOKEN" ]] && echo "CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
    [[ -n "$CLOUDFLARE_DOMAIN" ]]    && echo "CLOUDFLARE_DOMAIN=$CLOUDFLARE_DOMAIN"
  } > "$ROOT_ENV"
  log "Wrote $ROOT_ENV"
}
```

- [ ] **Step 2: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 3: Verify it writes a valid root .env to a temp path**

Run:
```bash
bash -c '
  source ./quickstart.sh
  ROOT_ENV=$(mktemp) FORCE=true VULTR_API_KEY="" CLOUDFLARE_API_TOKEN="" CLOUDFLARE_DOMAIN=""
  gen_root_env
  echo "--- generated ---"; cat "$ROOT_ENV"
  grep -qE "^SECRET_KEY=[0-9a-f]{64}$" "$ROOT_ENV" && echo "SECRET_KEY_OK"
  grep -q "^EVENT_QUOTA={" "$ROOT_ENV" && echo "QUOTA_OK"
  rm -f "$ROOT_ENV"
'
```
Expected: file with `SECRET_KEY=<64 hex>`, `DATABASE_URL=...`, `EVENT_QUOTA={...}`; prints `SECRET_KEY_OK` and `QUOTA_OK`.

- [ ] **Step 4: Verify optional vars are included when set**

Run:
```bash
bash -c '
  source ./quickstart.sh
  ROOT_ENV=$(mktemp) FORCE=true VULTR_API_KEY="vk_test" CLOUDFLARE_API_TOKEN="" CLOUDFLARE_DOMAIN=""
  gen_root_env
  grep -q "^VULTR_API_KEY=vk_test$" "$ROOT_ENV" && echo "VULTR_OK"
  rm -f "$ROOT_ENV"
'
```
Expected: prints `VULTR_OK`.

- [ ] **Step 5: Commit**

```bash
git add quickstart.sh
git commit -m "feat: generate root .env with SECRET_KEY and optional cloud keys"
```

---

### Task 5: Generate `deploy/.env` (bcrypt via docker + secrets)

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Add the `bcrypt_htpasswd` helper**

In `quickstart.sh`, immediately after the `escape_for_compose` function, add:

```bash
# Produce a compose-escaped "user:bcrypthash" string using a throwaway httpd
# container (avoids requiring apache2-utils on the host).
bcrypt_htpasswd() {
  local user="$1" pass="$2" raw
  raw="$($SUDO docker run --rm httpd:2.4-alpine htpasswd -nbB "$user" "$pass")" \
    || err "Failed to generate bcrypt hash via the httpd:2.4-alpine image."
  escape_for_compose "$raw"
}
```

- [ ] **Step 2: Replace the `gen_deploy_env` stub with the real implementation**

Replace this line:

```bash
gen_deploy_env()    { log "STUB gen_deploy_env"; }
```

with:

```bash
gen_deploy_env() {
  if [[ -f "$DEPLOY_ENV" && "$FORCE" != true ]]; then
    log "$DEPLOY_ENV exists — skipping"
    return
  fi
  backup_if_exists "$DEPLOY_ENV"

  TRAEFIK_PASSWORD="$(openssl rand -base64 18)"
  SEMAPHORE_ADMIN_PASSWORD="$(openssl rand -base64 18)"
  local enc pgpw traefik_auth
  enc="$(openssl rand -base64 32)"
  pgpw="$(openssl rand -base64 32)"
  traefik_auth="$(bcrypt_htpasswd "${TRAEFIK_USER:-admin}" "$TRAEFIK_PASSWORD")"

  {
    echo "DOMAIN=$DOMAIN"
    echo "ACME_EMAIL=$ACME_EMAIL"
    echo "CALDERA_AGENT_URL=http://$SERVER_IP:8888"
    echo "TRAEFIK_DASHBOARD_AUTH=$traefik_auth"
    echo "SEMAPHORE_ADMIN=admin"
    echo "SEMAPHORE_ADMIN_PASSWORD=$SEMAPHORE_ADMIN_PASSWORD"
    echo "SEMAPHORE_ADMIN_NAME=Admin"
    echo "SEMAPHORE_ADMIN_EMAIL=$ACME_EMAIL"
    echo "SEMAPHORE_ACCESS_KEY_ENCRYPTION=$enc"
    echo "SEMAPHORE_POSTGRES_PASSWORD=$pgpw"
    [[ -n "$VULTR_API_KEY" ]] && echo "VULTR_API_KEY=$VULTR_API_KEY"
  } > "$DEPLOY_ENV"
  log "Wrote $DEPLOY_ENV"
}
```

- [ ] **Step 3: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 4: Verify deploy/.env generation (bcrypt stubbed, no docker needed)**

Run:
```bash
bash -c '
  source ./quickstart.sh
  # Override the docker-dependent helper for an offline unit check.
  bcrypt_htpasswd() { echo "admin:\$\$2y\$\$05\$\$stub"; }
  DEPLOY_ENV=$(mktemp) FORCE=true
  DOMAIN=ex.com ACME_EMAIL=a@b.c SERVER_IP=1.2.3.4 TRAEFIK_USER=admin VULTR_API_KEY=""
  gen_deploy_env
  echo "--- generated ---"; cat "$DEPLOY_ENV"
  grep -q "^CALDERA_AGENT_URL=http://1.2.3.4:8888$" "$DEPLOY_ENV" && echo "AGENT_URL_OK"
  grep -q "^TRAEFIK_DASHBOARD_AUTH=admin:\$\$2y\$\$05\$\$stub$" "$DEPLOY_ENV" && echo "AUTH_ESCAPED_OK"
  grep -q "^SEMAPHORE_POSTGRES_PASSWORD=." "$DEPLOY_ENV" && echo "PG_OK"
  rm -f "$DEPLOY_ENV"
'
```
Expected: file contains all keys; prints `AGENT_URL_OK`, `AUTH_ESCAPED_OK`, `PG_OK`.

- [ ] **Step 5: (On the server, Docker present) Verify real bcrypt generation works**

Run: `bash -c 'source ./quickstart.sh; bcrypt_htpasswd admin hunter2'`
Expected: a string like `admin:$$2y$$05$$....` (dollar signs doubled). Skip if not on a Docker host.

- [ ] **Step 6: Commit**

```bash
git add quickstart.sh
git commit -m "feat: generate deploy/.env with bcrypt dashboard auth and secrets"
```

---

### Task 6: Generate Caldera `local.yml` with unique secrets

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Replace the `gen_caldera_config` stub with the real implementation**

Replace this line:

```bash
gen_caldera_config(){ log "STUB gen_caldera_config"; }
```

with:

```bash
gen_caldera_config() {
  if [[ -f "$CALDERA_CFG" && "$FORCE" != true ]]; then
    log "$CALDERA_CFG exists — skipping"
    return
  fi
  backup_if_exists "$CALDERA_CFG"
  cp "$CALDERA_CFG_EXAMPLE" "$CALDERA_CFG"

  # Replace each placeholder occurrence with its own freshly generated secret.
  # Hex avoids YAML-quoting edge cases for unquoted scalar values.
  local ph secret
  for ph in REPLACE_ME REPLACE_WITH_KEY_FILE_PASSPHRASE; do
    while grep -q "$ph" "$CALDERA_CFG"; do
      secret="$(openssl rand -hex 32)"
      awk -v ph="$ph" -v val="$secret" '
        BEGIN { done = 0 }
        {
          if (!done) {
            i = index($0, ph)
            if (i > 0) {
              $0 = substr($0, 1, i - 1) val substr($0, i + length(ph))
              done = 1
            }
          }
          print
        }' "$CALDERA_CFG" > "$CALDERA_CFG.tmp" && mv "$CALDERA_CFG.tmp" "$CALDERA_CFG"
    done
  done
  log "Wrote $CALDERA_CFG"
}
```

- [ ] **Step 2: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 3: Verify all secrets are filled, unique, and the key-file path is preserved**

Run:
```bash
bash -c '
  source ./quickstart.sh
  CALDERA_CFG=$(mktemp) FORCE=true
  gen_caldera_config
  echo "--- checks ---"
  ! grep -q "REPLACE_ME" "$CALDERA_CFG" && echo "NO_REPLACE_ME"
  ! grep -q "REPLACE_WITH_KEY_FILE_PASSPHRASE" "$CALDERA_CFG" && echo "NO_PASSPHRASE_PLACEHOLDER"
  grep -q "REPLACE_WITH_KEY_FILE_PATH" "$CALDERA_CFG" && echo "KEYFILE_PATH_PRESERVED"
  # blue and red api keys must differ
  bk=$(grep "^api_key_blue:" "$CALDERA_CFG" | awk "{print \$2}")
  rk=$(grep "^api_key_red:"  "$CALDERA_CFG" | awk "{print \$2}")
  [[ -n "$bk" && "$bk" != "$rk" ]] && echo "KEYS_UNIQUE"
  rm -f "$CALDERA_CFG"
'
```
Expected: prints `NO_REPLACE_ME`, `NO_PASSPHRASE_PLACEHOLDER`, `KEYFILE_PATH_PRESERVED`, `KEYS_UNIQUE`.

- [ ] **Step 4: Commit**

```bash
git add quickstart.sh
git commit -m "feat: generate Caldera local.yml with unique per-placeholder secrets"
```

---

### Task 7: Launch the stack and print the summary

**Files:**
- Modify: `quickstart.sh`

- [ ] **Step 1: Replace the `launch_stack` stub with the real implementation**

Replace this line:

```bash
launch_stack()      { log "STUB launch_stack"; }
```

with:

```bash
launch_stack() {
  log "Launching stack: docker compose up -d --build (from deploy/)"
  # Run from deploy/ so compose reads deploy/.env for interpolation while the
  # API service's `env_file: ../.env` resolves to the repo-root .env.
  ( cd "$REPO_ROOT/deploy" && $SUDO docker compose up -d --build ) \
    || err "docker compose up failed — check the output above."
}
```

- [ ] **Step 2: Replace the `print_summary` stub with the real implementation**

Replace this line:

```bash
print_summary()     { log "STUB print_summary"; }
```

with:

```bash
print_summary() {
  cat <<EOF

============================================================
 CTF-IT deployment started.

 Services (create a DNS A-record for each, pointing at this server):
   https://ctf.$DOMAIN        — CTF dashboard
   https://caldera.$DOMAIN    — MITRE Caldera
   https://semaphore.$DOMAIN  — Ansible Semaphore
   https://dockhand.$DOMAIN   — Container management
   https://traefik.$DOMAIN    — Traefik dashboard
EOF
  if [[ -n "$TRAEFIK_PASSWORD" ]]; then
    cat <<EOF

 Generated credentials (also stored in deploy/.env):
   Traefik dashboard:  ${TRAEFIK_USER:-admin} / $TRAEFIK_PASSWORD
   Semaphore admin:    admin / $SEMAPHORE_ADMIN_PASSWORD
EOF
  else
    cat <<EOF

 Existing deploy/.env was reused — see that file for credentials.
EOF
  fi
  cat <<EOF

 Next steps (manual):
   - Create the DNS A-records listed above.
   - Optional: build the per-user CTF base image (see README).
   - Optional: export the Caldera plugin from the CTF admin UI.
============================================================
EOF
}
```

- [ ] **Step 3: Verify syntax and shellcheck**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors.

- [ ] **Step 4: Verify the summary renders with generated credentials**

Run:
```bash
bash -c '
  source ./quickstart.sh
  DOMAIN=ex.com TRAEFIK_USER=admin TRAEFIK_PASSWORD=pw1 SEMAPHORE_ADMIN_PASSWORD=pw2
  print_summary
'
```
Expected: prints the service URLs under `ex.com` and a credentials block showing `admin / pw1` and `admin / pw2`.

- [ ] **Step 5: Verify the summary falls back when no credentials were generated**

Run: `bash -c 'source ./quickstart.sh; DOMAIN=ex.com; print_summary' | grep -q "Existing deploy/.env was reused" && echo FALLBACK_OK`
Expected: prints `FALLBACK_OK`.

- [ ] **Step 6: Commit**

```bash
git add quickstart.sh
git commit -m "feat: launch stack from deploy/ and print deployment summary"
```

---

### Task 8: README usage note and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a Quickstart section to the README**

Find the deployment/installation area of `README.md` (search for `docker compose` or a "Deployment" heading). Immediately before that content, add:

```markdown
## Quickstart (production server)

On a fresh Linux server, deploy the full stack with one command:

```bash
git clone <repo-url> CTF-IT && cd CTF-IT
./quickstart.sh
```

The script installs Docker if needed, prompts for your domain, Let's Encrypt
email, and server IP, generates all secrets and config files (root `.env`,
`deploy/.env`, and `deploy/caldera/config/local.yml`), then brings up
`deploy/docker-compose.yml`. It is idempotent — re-running skips any config file
that already exists. Use `--force` to regenerate (existing files are backed up
first) or `--non-interactive` to read inputs from environment variables.

After it finishes, create DNS A-records for `ctf`, `caldera`, `semaphore`,
`dockhand`, and `traefik` under your domain, pointing at the server.
```

(If the README has no deployment section, append this block at the end of the file.)

- [ ] **Step 2: Verify the README renders the code fences correctly**

Run: `grep -n "Quickstart (production server)" README.md`
Expected: prints the matching heading line.

- [ ] **Step 3: Final full syntax + shellcheck pass**

Run: `bash -n quickstart.sh && (command -v shellcheck >/dev/null && shellcheck quickstart.sh || echo "shellcheck skipped")`
Expected: no errors/warnings.

- [ ] **Step 4: Verify idempotency — all config present means straight to launch**

Run:
```bash
bash -c '
  source ./quickstart.sh
  ROOT_ENV=$(mktemp) DEPLOY_ENV=$(mktemp) CALDERA_CFG=$(mktemp)
  collect_inputs   # should skip (all present, FORCE=false)
  gen_root_env; gen_deploy_env; gen_caldera_config
  rm -f "$ROOT_ENV" "$DEPLOY_ENV" "$CALDERA_CFG"
'
```
Expected: `==> All env files present — skipping input collection`, then three `... exists — skipping` lines. No secrets regenerated.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document quickstart.sh in README"
```

---

## End-to-End Verification

After all tasks are complete:

- [ ] **1. Static checks pass**

```bash
bash -n quickstart.sh
shellcheck quickstart.sh   # if installed
```
Expected: clean.

- [ ] **2. Idempotency check (local, no side effects)**

Run the Task 8 Step 4 snippet. Expected: skip messages only, no regeneration.

- [ ] **3. Fresh-server smoke test (manual, on a clean Ubuntu VPS — not this machine)**

```bash
git clone <repo-url> CTF-IT && cd CTF-IT
./quickstart.sh
```
Verify:
- [ ] Docker is installed (if it was absent) and `docker compose version` works.
- [ ] `./.env`, `deploy/.env`, and `deploy/caldera/config/local.yml` are created with filled-in secrets (no `REPLACE_ME` remaining in the Caldera config).
- [ ] `cd deploy && docker compose ps` shows traefik, dockhand, api, caldera, semaphore, and semaphore-postgres running; health checks pass.
- [ ] `https://ctf.$DOMAIN` serves the CTF dashboard over a valid Let's Encrypt certificate (after DNS A-records resolve).
- [ ] The Traefik dashboard at `https://traefik.$DOMAIN` accepts the printed admin/password.
- [ ] Semaphore at `https://semaphore.$DOMAIN` accepts `admin` / the printed password.
- [ ] Re-running `./quickstart.sh` reports the three config files as "exists — skipping" and does not disrupt the running stack.
```
