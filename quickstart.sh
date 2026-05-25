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
