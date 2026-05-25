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
