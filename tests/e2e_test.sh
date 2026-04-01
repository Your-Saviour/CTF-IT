#!/usr/bin/env bash
#
# End-to-end integration test for the CTF platform.
# Automates every step from TEST_PLAN.md: register, build, pull, run,
# verify (unfixed), fix all modules, verify (fixed), and idempotency check.
#
# Usage:  ./tests/e2e_test.sh
# Prereqs: Docker running, port 5050 free, .env.test in project root,
#          base image built (see TEST_PLAN.md)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# --- Colours & helpers -------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
COOKIES="/tmp/ctf-e2e-cookies-$$.txt"
CONTAINER_NAME="ctf-e2e-test-$$"
IMAGE_TAG=""
E2E_STARTED_COMPOSE=false
ENV_BACKED_UP=false

log()  { echo -e "${CYAN}[E2E]${NC} $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL + 1)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

cleanup() {
    log "Cleaning up..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    if [ -n "$IMAGE_TAG" ]; then
        docker rmi "localhost:5050/$IMAGE_TAG" 2>/dev/null || true
    fi
    # Tear down compose (preserve volumes unless E2E_CLEAN_VOLUMES=true)
    if [ "$E2E_STARTED_COMPOSE" = "true" ]; then
        if [ "${E2E_CLEAN_VOLUMES:-false}" = "true" ]; then
            docker compose down -v 2>/dev/null || true
        else
            docker compose down 2>/dev/null || true
        fi
    fi
    # Restore original .env if we backed it up
    if [ "$ENV_BACKED_UP" = "true" ] && [ -f .env.bak ]; then
        mv .env.bak .env
    fi
    rm -f "$COOKIES"
}
trap cleanup EXIT

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        pass "$label"
    else
        fail "$label (expected='$expected', got='$actual')"
    fi
}

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -q "$needle"; then
        pass "$label"
    else
        fail "$label (expected to contain '$needle')"
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -q "$needle"; then
        fail "$label (should NOT contain '$needle')"
    else
        pass "$label"
    fi
}

json_field() {
    python3 -c "import sys,json; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null
}

# --- Preflight checks --------------------------------------------------------
log "=== Preflight ==="

if ! docker info &>/dev/null; then
    fail "Docker is not running"; exit 1
fi

if [ ! -f ".env.test" ]; then
    fail ".env.test not found"; exit 1
fi

if ! docker image inspect ctf-base:latest &>/dev/null; then
    warn "ctf-base:latest not found — building base image..."
    source base/.env 2>/dev/null || true
    ROOT_PASSWORD="${ROOT_PASSWORD:-changeme123}"
    docker build --build-arg ROOT_PASSWORD="$ROOT_PASSWORD" -t ctf-base:latest base/
fi

pass "Preflight checks OK"

# --- Setup: clean slate + start services -------------------------------------
log "=== Setup ==="

# Check if compose is already running; if so, tear it down fresh for a clean test
E2E_STARTED_COMPOSE=true
if [ "${E2E_CLEAN_VOLUMES:-false}" = "true" ]; then
    docker compose down -v 2>/dev/null || true
else
    docker compose down 2>/dev/null || true
fi
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Back up existing .env so we can restore it after the test
if [ -f .env ]; then
    cp .env .env.bak
    ENV_BACKED_UP=true
fi
cp .env.test .env
docker compose build --quiet
docker compose up -d

# Wait for both services to be healthy
log "Waiting for services..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/ >/dev/null 2>&1 && \
       curl -sf http://localhost:5050/v2/_catalog >/dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq 30 ]; then
        fail "Services did not start within 60s"
        docker compose logs
        exit 1
    fi
    sleep 2
done

CATALOG=$(curl -s http://localhost:5050/v2/_catalog)
assert_contains "Registry healthy" "$CATALOG" '"repositories"'
pass "Services running"

# --- Step 1: Register user and build image -----------------------------------
log "=== Step 1: Register & Build ==="

HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8000/auth/register \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=e2e_test&password=testpass123" \
    -c "$COOKIES")

# Registration returns 303 redirect to /dashboard and sets session cookie
assert_eq "Register returns 303 redirect" "303" "$HTTP_CODE"

# Poll build status
log "Polling build status..."
BUILD_STATUS="unknown"
for i in $(seq 1 60); do
    BUILD_STATUS=$(curl -s -b "$COOKIES" http://localhost:8000/api/images/status | json_field status)
    if [ "$BUILD_STATUS" = "ready" ] || [ "$BUILD_STATUS" = "failed" ]; then
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        log "  attempt $i: status=$BUILD_STATUS"
    fi
    sleep 2
done

assert_eq "Image build succeeded" "ready" "$BUILD_STATUS"

if [ "$BUILD_STATUS" != "ready" ]; then
    fail "Build failed — aborting"
    docker compose logs api
    exit 1
fi

# --- Step 2: Verify registry push -------------------------------------------
log "=== Step 2: Registry Push ==="

CATALOG=$(curl -s http://localhost:5050/v2/_catalog)
assert_contains "Image in registry catalog" "$CATALOG" "ctf-"

PULL_CMD_RESP=$(curl -s -b "$COOKIES" http://localhost:8000/api/images/pull-command)
assert_contains "Pull command has registry prefix" "$PULL_CMD_RESP" "localhost:5050"

# --- Step 3: Pull and run user container -------------------------------------
log "=== Step 3: Pull & Run Container ==="

IMAGE_TAG=$(curl -s -b "$COOKIES" http://localhost:8000/api/images/status \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['image_tag'])")

log "Image tag: $IMAGE_TAG"

docker pull "localhost:5050/$IMAGE_TAG"
pass "Image pulled from registry"

docker run -d --name "$CONTAINER_NAME" \
    --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    "localhost:5050/$IMAGE_TAG"

# Wait for systemd to initialize
sleep 5

CONTAINER_RUNNING=$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
if [ "$CONTAINER_RUNNING" != "true" ]; then
    fail "Container is running (expected='true', got='$CONTAINER_RUNNING')"
    warn "Container logs:"
    docker logs "$CONTAINER_NAME" 2>&1 | tail -20
    warn "If systemd fails to start, try: docker run --privileged ..."
else
    pass "Container is running"
fi

# --- Step 4: Verify container state file -------------------------------------
log "=== Step 4: Container State File ==="

STATE_JSON=$(docker exec "$CONTAINER_NAME" cat /opt/ctf/state.json)
assert_contains "state.json has user_id" "$STATE_JSON" "user_id"
assert_contains "state.json has shadow_hashes" "$STATE_JSON" "shadow_hashes"

CTF_FILES=$(docker exec "$CONTAINER_NAME" ls /opt/ctf/)
assert_contains "audit.py present" "$CTF_FILES" "audit.py"
assert_contains "state.json present" "$CTF_FILES" "state.json"
assert_not_contains "No manifest.json" "$CTF_FILES" "manifest.json"
assert_not_contains "No collect.py" "$CTF_FILES" "collect.py"

# --- Step 5: Submit unfixed state — all should fail --------------------------
log "=== Step 5: Verify Unfixed State ==="

PAYLOAD=$(docker exec "$CONTAINER_NAME" python3 /opt/ctf/audit.py)
UNFIXED_RESP=$(curl -s -b "$COOKIES" -X POST http://localhost:8000/api/verify \
    -H "Content-Type: application/json" -d "$PAYLOAD")

UNFIXED_TOTAL=$(echo "$UNFIXED_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_points'])")
assert_eq "Unfixed: total_points=0" "0" "$UNFIXED_TOTAL"

UNFIXED_ALL_FAIL=$(echo "$UNFIXED_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('true' if all(not r['passed'] for r in data['results']) else 'false')
")
assert_eq "Unfixed: all modules failed" "true" "$UNFIXED_ALL_FAIL"

NUM_MODULES=$(echo "$UNFIXED_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['results']))")
assert_eq "9 modules assigned" "9" "$NUM_MODULES"
log "Modules verified: $NUM_MODULES"

# --- Step 6: Apply all fixes -------------------------------------------------
log "=== Step 6: Apply All Fixes ==="

# world_writable_shadow (vulnerability - file_permissions)
docker exec "$CONTAINER_NAME" bash -c "chmod 640 /etc/shadow && chown root:shadow /etc/shadow"
log "  Fixed: world_writable_shadow"

# suid_find (vulnerability - file_permissions)
docker exec "$CONTAINER_NAME" bash -c "chmod 755 /usr/bin/find"
log "  Fixed: suid_find"

# writable_cron_script (vulnerability - file_permissions)
docker exec "$CONTAINER_NAME" bash -c "chmod 750 /opt/maintenance.sh"
log "  Fixed: writable_cron_script"

# nopasswd_sudo (vulnerability - file_not_contains)
docker exec "$CONTAINER_NAME" bash -c "rm /etc/sudoers.d/backdoor"
log "  Fixed: nopasswd_sudo"

# unauthorized_ssh_key (vulnerability - file_not_contains)
docker exec "$CONTAINER_NAME" bash -c "sed -i '/rogue@backdoor/d' /root/.ssh/authorized_keys"
log "  Fixed: unauthorized_ssh_key"

# disable_ssh_root_login (hardening - file_contains)
docker exec "$CONTAINER_NAME" bash -c "sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config"
log "  Fixed: disable_ssh_root_login"

# change_root_password (hardening - password_changed)
docker exec "$CONTAINER_NAME" bash -c "echo 'root:N3wTestP@ssw0rd!' | chpasswd"
log "  Fixed: change_root_password"

# setup_ssh_key_auth (hardening - file_contains)
docker exec "$CONTAINER_NAME" bash -c "sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config"
log "  Fixed: setup_ssh_key_auth"

# install_fail2ban (hardening - service_running)
docker exec "$CONTAINER_NAME" bash -c "apt-get update -qq && apt-get install -y -qq fail2ban > /dev/null 2>&1 && systemctl start fail2ban && systemctl enable fail2ban"
log "  Fixed: install_fail2ban"

pass "All fixes applied"

# --- Step 7: Submit fixed state — all should pass ----------------------------
log "=== Step 7: Verify Fixed State ==="

PAYLOAD=$(docker exec "$CONTAINER_NAME" python3 /opt/ctf/audit.py)
FIXED_RESP=$(curl -s -b "$COOKIES" -X POST http://localhost:8000/api/verify \
    -H "Content-Type: application/json" -d "$PAYLOAD")

FIXED_TOTAL=$(echo "$FIXED_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_points'])")
assert_eq "Fixed: total_points=1500" "1500" "$FIXED_TOTAL"

FIXED_ALL_PASS=$(echo "$FIXED_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('true' if all(r['passed'] for r in data['results']) else 'false')
")
assert_eq "Fixed: all modules passed" "true" "$FIXED_ALL_PASS"

# Verify individual module results
echo "$FIXED_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
expected = {
    'world_writable_shadow': 200,
    'suid_find': 100,
    'writable_cron_script': 200,
    'nopasswd_sudo': 200,
    'unauthorized_ssh_key': 200,
    'disable_ssh_root_login': 100,
    'change_root_password': 100,
    'install_fail2ban': 200,
    'setup_ssh_key_auth': 200,
}
for r in data['results']:
    mid = r['module_id']
    pts = r['points_awarded']
    exp = expected.get(mid)
    if exp is not None and pts == exp:
        print(f'  PASS  {mid}: {pts} pts')
    elif exp is not None:
        print(f'  FAIL  {mid}: expected {exp}, got {pts}')
    else:
        print(f'  WARN  unknown module: {mid}')
"

# --- Step 8: Idempotency — no double points ----------------------------------
log "=== Step 8: Idempotency Check ==="

PAYLOAD=$(docker exec "$CONTAINER_NAME" python3 /opt/ctf/audit.py)
IDEM_RESP=$(curl -s -b "$COOKIES" -X POST http://localhost:8000/api/verify \
    -H "Content-Type: application/json" -d "$PAYLOAD")

IDEM_TOTAL=$(echo "$IDEM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_points'])")
assert_eq "Idempotent: total_points still 1500" "1500" "$IDEM_TOTAL"

IDEM_ZERO_AWARDED=$(echo "$IDEM_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('true' if all(r['points_awarded'] == 0 for r in data['results']) else 'false')
")
assert_eq "Idempotent: no new points awarded" "true" "$IDEM_ZERO_AWARDED"

IDEM_ALL_PASS=$(echo "$IDEM_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('true' if all(r['passed'] for r in data['results']) else 'false')
")
assert_eq "Idempotent: all still passed" "true" "$IDEM_ALL_PASS"

# --- Step 9: Scoreboard check -----------------------------------------------
log "=== Step 9: Scoreboard ==="

SCOREBOARD=$(curl -s http://localhost:8000/api/scoreboard)
SB_USER=$(echo "$SCOREBOARD" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for entry in data:
    if entry['username'] == 'e2e_test':
        print(entry['total_points'])
        break
else:
    print('not_found')
")
assert_eq "Scoreboard shows 1500 for e2e_test" "1500" "$SB_USER"

# --- Summary -----------------------------------------------------------------
echo ""
echo "================================================"
echo -e "  ${GREEN}PASSED: $PASS${NC}  ${RED}FAILED: $FAIL${NC}"
echo "================================================"

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}E2E test FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}E2E test PASSED${NC}"
    exit 0
fi
