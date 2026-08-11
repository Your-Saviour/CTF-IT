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
umask 077

# ── Globals ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ROOT_ENV="$REPO_ROOT/.env"
DEPLOY_ENV="$REPO_ROOT/deploy/.env"
CALDERA_CFG="$REPO_ROOT/deploy/caldera/config/local.yml"
CALDERA_CFG_EXAMPLE="$REPO_ROOT/deploy/caldera/config/local.yml.example"
CALDERA_SSH_KEY="$REPO_ROOT/deploy/caldera/config/ssh_host_key"

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
AI_API_BASE="${AI_API_BASE:-}"
AI_API_KEY="${AI_API_KEY:-}"
AI_MODEL="${AI_MODEL:-}"

# Generated plaintext (printed in summary)
TRAEFIK_PASSWORD=""
SEMAPHORE_ADMIN_PASSWORD=""
ADMIN_BOOTSTRAP_TOKEN=""

# ── Logging helpers ──────────────────────────────────────────────────────────
log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
err()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || err "Required command not found: $1"; }
read_env_value() { awk -F= -v key="$2" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$1"; }
require_env_value() {
  local file="$1" key="$2"
  [[ -n "$(read_env_value "$file" "$key")" ]] \
    || err "Missing required $key in $file. Re-run with --force or add the value."
}

# Double every '$' so docker-compose does not interpolate bcrypt hashes.
escape_for_compose() { printf '%s' "$1" | sed 's/\$/\$\$/g'; }

# Produce a compose-escaped "user:bcrypthash" string using a throwaway httpd
# container (avoids requiring apache2-utils on the host).
bcrypt_htpasswd() {
  local user="$1" pass="$2" raw
  raw="$($SUDO docker run --rm httpd:2.4-alpine htpasswd -nbB "$user" "$pass")" \
    || err "Failed to generate bcrypt hash via the httpd:2.4-alpine image."
  escape_for_compose "$raw"
}

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

set_yaml_scalar() {
  local file="$1" key="$2" value="$3"
  awk -v key="$key" -v value="$value" '
    index($0, key ":") == 1 { print key ": " value; next }
    { print }
  ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
}

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
  VULTR_API_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_DOMAIN, AI_API_BASE,
  AI_API_KEY, AI_MODEL.
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

# ── Deployment phases ────────────────────────────────────────────────────────
phase_preflight() {
  log "Preflight checks"
  [[ "$(uname -s)" == "Linux" ]] || err "This script must run on Linux (got $(uname -s))."
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || err "Not running as root and 'sudo' not found."
    SUDO="sudo"
  fi
  require_cmd openssl
  require_cmd sed
  require_cmd awk
  [[ -f "$REPO_ROOT/.env.example" ]] || err "Missing $REPO_ROOT/.env.example"
  [[ -f "$REPO_ROOT/deploy/.env.example" ]] || err "Missing $REPO_ROOT/deploy/.env.example"
  [[ -f "$CALDERA_CFG_EXAMPLE" ]] || err "Missing $CALDERA_CFG_EXAMPLE"
}

phase_docker() {
  log "Checking Docker"
  if command -v docker >/dev/null 2>&1 && $SUDO docker compose version >/dev/null 2>&1; then
    log "Docker and compose plugin present — skipping install"
    return
  fi
  command -v apt-get >/dev/null 2>&1 \
    || err "Automatic Docker installation currently supports apt-based Linux distributions only. Install Docker Engine and Compose, then rerun."
  log "Installing Docker from the configured operating-system package repositories"
  $SUDO apt-get update || err "Package index update failed."
  $SUDO apt-get install -y docker.io docker-compose-v2 \
    || err "Docker installation failed. Install Docker Engine and the Compose v2 plugin, then rerun."
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  command -v docker >/dev/null 2>&1 && $SUDO docker compose version >/dev/null 2>&1 \
    || err "Docker still unavailable after install."
}
collect_inputs() {
  local need_deploy=false need_root=false
  { [[ -f "$DEPLOY_ENV" ]] && [[ "$FORCE" != true ]]; } || need_deploy=true
  { [[ -f "$ROOT_ENV" ]]   && [[ "$FORCE" != true ]]; } || need_root=true

  if [[ -f "$DEPLOY_ENV" ]]; then
    [[ -n "$DOMAIN" ]] || DOMAIN="$(read_env_value "$DEPLOY_ENV" DOMAIN)"
    [[ -n "$ACME_EMAIL" ]] || ACME_EMAIL="$(read_env_value "$DEPLOY_ENV" ACME_EMAIL)"
    [[ -n "$TRAEFIK_USER" ]] || TRAEFIK_USER="admin"
  fi

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
  prompt_var AI_API_BASE "OpenAI-compatible API URL (optional, blank to configure later)" "" false
  prompt_var AI_API_KEY "AI provider API key (optional, blank to configure later)" "" false
  prompt_var AI_MODEL "AI model ID" "gpt-4o" false
}
gen_root_env() {
  if [[ -f "$ROOT_ENV" && "$FORCE" != true ]]; then
    if ! grep -q '^SECRET_KEY=' "$ROOT_ENV"; then
      echo "SECRET_KEY=$(openssl rand -hex 32)" >> "$ROOT_ENV"
      log "Added missing SECRET_KEY to $ROOT_ENV"
    fi
    if ! grep -q '^ADMIN_BOOTSTRAP_TOKEN=' "$ROOT_ENV"; then
      ADMIN_BOOTSTRAP_TOKEN="$(openssl rand -hex 32)"
      echo "ADMIN_BOOTSTRAP_TOKEN=$ADMIN_BOOTSTRAP_TOKEN" >> "$ROOT_ENV"
      log "Added missing ADMIN_BOOTSTRAP_TOKEN to $ROOT_ENV"
    fi
    if ! grep -q '^DATA_ENCRYPTION_KEY=' "$ROOT_ENV"; then
      echo "DATA_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> "$ROOT_ENV"
      log "Added missing DATA_ENCRYPTION_KEY to $ROOT_ENV"
    fi
    if ! grep -q '^AGENT_API_KEY=' "$ROOT_ENV"; then
      echo "AGENT_API_KEY=$(openssl rand -hex 32)" >> "$ROOT_ENV"
      log "Added missing AGENT_API_KEY to $ROOT_ENV"
    fi
    if ! grep -q '^CTF_API_KEY=' "$ROOT_ENV"; then
      echo "CTF_API_KEY=$(openssl rand -hex 32)" >> "$ROOT_ENV"
      log "Added missing CTF_API_KEY to $ROOT_ENV"
    fi
    if [[ -n "$AI_API_BASE" ]] && ! grep -q '^AI_API_BASE=' "$ROOT_ENV"; then
      echo "AI_API_BASE=$AI_API_BASE" >> "$ROOT_ENV"
    fi
    if [[ -n "$AI_API_KEY" ]] && ! grep -q '^AI_API_KEY=' "$ROOT_ENV"; then
      echo "AI_API_KEY=$AI_API_KEY" >> "$ROOT_ENV"
    fi
    if [[ -n "$AI_MODEL" ]] && ! grep -q '^AI_MODEL=' "$ROOT_ENV"; then
      echo "AI_MODEL=$AI_MODEL" >> "$ROOT_ENV"
    fi
    chmod 600 "$ROOT_ENV"
    log "$ROOT_ENV exists — skipping"
    return
  fi
  backup_if_exists "$ROOT_ENV"
  local secret_key data_encryption_key agent_api_key ctf_api_key quota
  secret_key="$(openssl rand -hex 32)"
  data_encryption_key="$(openssl rand -hex 32)"
  ADMIN_BOOTSTRAP_TOKEN="$(openssl rand -hex 32)"
  agent_api_key="$(openssl rand -hex 32)"
  ctf_api_key="$(openssl rand -hex 32)"
  quota="$(grep -E '^EVENT_QUOTA=' "$REPO_ROOT/.env.example" | head -n1 | cut -d= -f2-)"
  {
    echo "SECRET_KEY=$secret_key"
    echo "ADMIN_BOOTSTRAP_TOKEN=$ADMIN_BOOTSTRAP_TOKEN"
    echo "DATA_ENCRYPTION_KEY=$data_encryption_key"
    echo "AGENT_API_KEY=$agent_api_key"
    echo "CTF_API_KEY=$ctf_api_key"
    echo "EVENT_QUOTA=$quota"
    [[ -n "$VULTR_API_KEY" ]]        && echo "VULTR_API_KEY=$VULTR_API_KEY"
    [[ -n "$CLOUDFLARE_API_TOKEN" ]] && echo "CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
    [[ -n "$CLOUDFLARE_DOMAIN" ]]    && echo "CLOUDFLARE_DOMAIN=$CLOUDFLARE_DOMAIN"
    [[ -n "$AI_API_BASE" ]]          && echo "AI_API_BASE=$AI_API_BASE"
    [[ -n "$AI_API_KEY" ]]           && echo "AI_API_KEY=$AI_API_KEY"
    [[ -n "$AI_MODEL" ]]             && echo "AI_MODEL=$AI_MODEL"
  } > "$ROOT_ENV"
  chmod 600 "$ROOT_ENV"
  log "Wrote $ROOT_ENV"
}
validate_configuration() {
  local key
  log "Validating generated configuration"
  for key in SECRET_KEY ADMIN_BOOTSTRAP_TOKEN DATA_ENCRYPTION_KEY AGENT_API_KEY CTF_API_KEY; do
    require_env_value "$ROOT_ENV" "$key"
  done
  for key in DOMAIN ACME_EMAIL CALDERA_AGENT_URL TRAEFIK_DASHBOARD_AUTH \
      SEMAPHORE_ADMIN_PASSWORD SEMAPHORE_ACCESS_KEY_ENCRYPTION \
      SEMAPHORE_POSTGRES_PASSWORD CTF_POSTGRES_PASSWORD; do
    require_env_value "$DEPLOY_ENV" "$key"
  done
  [[ -s "$CALDERA_SSH_KEY" ]] || err "Caldera SSH host key was not generated."
  if grep -q 'REPLACE_' "$CALDERA_CFG"; then
    err "Caldera configuration still contains unresolved REPLACE_ placeholders."
  fi
  if [[ -n "$(read_env_value "$ROOT_ENV" CLOUDFLARE_API_TOKEN)" ]] \
      && [[ -z "$(read_env_value "$ROOT_ENV" CLOUDFLARE_DOMAIN)" ]]; then
    err "CLOUDFLARE_API_TOKEN is set but CLOUDFLARE_DOMAIN is missing in $ROOT_ENV."
  fi
  (cd "$REPO_ROOT/deploy" && $SUDO docker compose config --quiet) \
    || err "Generated Docker Compose configuration is invalid."
  [[ -n "$(read_env_value "$ROOT_ENV" AI_API_BASE)" ]] \
    || warn "AI_API_BASE is not configured; the AI-agent UI will run, but LLM actions will be unavailable."
  [[ -n "$(read_env_value "$ROOT_ENV" VULTR_API_KEY)" ]] \
    || warn "VULTR_API_KEY is not configured; automatic cloud VM provisioning will be unavailable."
}
gen_deploy_env() {
  if [[ -f "$DEPLOY_ENV" && "$FORCE" != true ]]; then
    if ! grep -q '^CTF_POSTGRES_PASSWORD=' "$DEPLOY_ENV"; then
      echo "CTF_POSTGRES_PASSWORD=$(openssl rand -hex 32)" >> "$DEPLOY_ENV"
      log "Added missing CTF_POSTGRES_PASSWORD to $DEPLOY_ENV"
    fi
    chmod 600 "$DEPLOY_ENV"
    log "$DEPLOY_ENV exists — skipping"
    return
  fi
  backup_if_exists "$DEPLOY_ENV"

  TRAEFIK_PASSWORD="$(openssl rand -base64 18)"
  SEMAPHORE_ADMIN_PASSWORD="$(openssl rand -base64 18)"
  local enc pgpw ctfpgpw traefik_auth
  enc="$(openssl rand -base64 32)"
  pgpw="$(openssl rand -base64 32)"
  ctfpgpw="$(openssl rand -hex 32)"
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
    echo "CTF_POSTGRES_PASSWORD=$ctfpgpw"
    [[ -n "$VULTR_API_KEY" ]] && echo "VULTR_API_KEY=$VULTR_API_KEY"
  } > "$DEPLOY_ENV"
  chmod 600 "$DEPLOY_ENV"
  log "Wrote $DEPLOY_ENV"
}
gen_caldera_config() {
  if [[ -f "$CALDERA_CFG" && -f "$CALDERA_SSH_KEY" && "$FORCE" != true ]] \
      && ! grep -q 'REPLACE_' "$CALDERA_CFG"; then
    log "$CALDERA_CFG exists — skipping"
    return
  fi
  backup_if_exists "$CALDERA_CFG"
  backup_if_exists "$CALDERA_SSH_KEY"
  if [[ ! -f "$CALDERA_CFG" || "$FORCE" == true ]]; then
    cp "$CALDERA_CFG_EXAMPLE" "$CALDERA_CFG"
  fi

  local ssh_key_passphrase
  ssh_key_passphrase="$(openssl rand -hex 32)"
  openssl genpkey -algorithm RSA -aes-256-cbc \
    -pass "pass:$ssh_key_passphrase" -pkeyopt rsa_keygen_bits:3072 \
    -out "$CALDERA_SSH_KEY" >/dev/null 2>&1 \
    || err "Failed to generate the Caldera SSH tunnel host key."
  set_yaml_scalar "$CALDERA_CFG" "app.contact.tunnel.ssh.host_key_file" "/usr/src/app/conf/ssh_host_key"
  set_yaml_scalar "$CALDERA_CFG" "app.contact.tunnel.ssh.host_key_passphrase" "$ssh_key_passphrase"

  # Replace each placeholder occurrence with its own freshly generated secret.
  # Hex avoids YAML-quoting edge cases for unquoted scalar values.
  local ph secret
  for ph in REPLACE_ME; do
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
  chmod 600 "$CALDERA_CFG" "$CALDERA_SSH_KEY"
  log "Wrote $CALDERA_CFG and $CALDERA_SSH_KEY"
}
launch_stack() {
  log "Launching stack: docker compose up -d --build (from deploy/)"
  # Run from deploy/ so compose reads deploy/.env for interpolation while the
  # API service's `env_file: ../.env` resolves to the repo-root .env.
  ( cd "$REPO_ROOT/deploy" && $SUDO docker compose up -d --build ) \
    || err "docker compose up failed — check the output above."
}

wait_for_stack() {
  local timeout=600 deadline output unhealthy expected_count actual_count
  deadline=$((SECONDS + timeout))
  expected_count="$(cd "$REPO_ROOT/deploy" && $SUDO docker compose config --services | wc -l | tr -d ' ')"
  log "Waiting for production services to become healthy (up to ${timeout}s)"
  while (( SECONDS < deadline )); do
    output="$(cd "$REPO_ROOT/deploy" && $SUDO docker compose ps --format '{{.Service}} {{.State}} {{.Health}}')"
    actual_count="$(printf '%s\n' "$output" | awk 'NF {count++} END {print count+0}')"
    unhealthy="$(printf '%s\n' "$output" | awk '$2 != "running" || ($3 != "" && $3 != "-" && $3 != "healthy") {print}')"
    if [[ "$actual_count" == "$expected_count" && -z "$unhealthy" ]]; then
      log "All production services are running and healthy"
      return
    fi
    sleep 5
  done
  (cd "$REPO_ROOT/deploy" && $SUDO docker compose ps) || true
  err "The production stack did not become healthy within ${timeout}s. Run 'cd deploy && docker compose logs --tail=200' for details."
}

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
  if [[ -n "$ADMIN_BOOTSTRAP_TOKEN" ]]; then
    cat <<EOF

 Initial administrator token (also stored in .env):
   $ADMIN_BOOTSTRAP_TOKEN
EOF
  fi
  cat <<EOF

 Next steps (manual):
   - Create the DNS A-records listed above.
   - Optional: export the Caldera plugin from the CTF admin UI.
============================================================
EOF
}

main() {
  parse_args "$@"
  phase_preflight
  phase_docker
  collect_inputs
  gen_root_env
  gen_deploy_env
  gen_caldera_config
  chmod 600 "$ROOT_ENV" "$DEPLOY_ENV" "$CALDERA_CFG" "$CALDERA_SSH_KEY"
  validate_configuration
  launch_stack
  wait_for_stack
  print_summary
}

# Run main only on direct execution (sourceable for testing).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
