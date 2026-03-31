# Test Plan

End-to-end integration test for the CTF platform. Run this after modifying modules, verification logic, the builder, or collect.py.

## Prerequisites

- Docker running locally
- `.env.test` exists in project root (gitignored) with:
  - `SECRET_KEY`, `DATABASE_URL`, `EVENT_QUOTA`, `ROOT_PASSWORD`
- Base image built: `source base/.env && docker build --build-arg ROOT_PASSWORD=$ROOT_PASSWORD -t ctf-base:latest base/`

## Test Setup

```bash
# Clean slate
docker compose down 2>/dev/null
docker rm -f ctf-e2e-test 2>/dev/null
rm -f ctf.db

# Use test env (selects all modules)
cp .env.test .env

# Rebuild and start API
docker compose build && docker compose up -d
sleep 3
```

## Step 1: Register User and Build Image

```bash
# Register — triggers background image build
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=e2e_test&password=testpass123" \
  -c /tmp/ctf-test-cookies.txt -D - -o /dev/null

# Poll until ready (should take <30s)
for i in $(seq 1 30); do
  img_status=$(curl -s -b /tmp/ctf-test-cookies.txt \
    http://localhost:8000/api/images/status \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
  echo "Attempt $i: $img_status"
  if [ "$img_status" = "ready" ] || [ "$img_status" = "failed" ]; then break; fi
  sleep 2
done
```

**Expected:** Status reaches `ready`. If `failed`, check API logs: `docker compose logs api`.

## Step 2: Run User Container

```bash
# Get image tag
IMAGE_TAG=$(curl -s -b /tmp/ctf-test-cookies.txt \
  http://localhost:8000/api/images/status \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['image_tag'])")

# Run with systemd support
docker run -d --name ctf-e2e-test \
  --cap-add SYS_ADMIN --cap-add NET_ADMIN --cgroupns=private \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  $IMAGE_TAG

sleep 3
```

## Step 3: Verify Manifest

```bash
docker exec ctf-e2e-test cat /opt/ctf/manifest.json | python3 -m json.tool
```

**Expected:** All selected modules present with correct verification specs. For `password_changed` modules, `original_hash` field must be populated (not empty, not missing).

## Step 4: Submit Unfixed State — All Should Fail

```bash
PAYLOAD=$(docker exec ctf-e2e-test python3 /opt/ctf/collect.py)
curl -s -b /tmp/ctf-test-cookies.txt -X POST http://localhost:8000/api/verify \
  -H "Content-Type: application/json" -d "$PAYLOAD" | python3 -m json.tool
```

**Expected:** Every module returns `"passed": false`, `"points_awarded": 0`, `"total_points": 0`.

## Step 5: Apply All Fixes

```bash
# world_writable_shadow (vulnerability - file_permissions)
docker exec ctf-e2e-test bash -c "chmod 640 /etc/shadow && chown root:shadow /etc/shadow"

# suid_find (vulnerability - file_permissions)
docker exec ctf-e2e-test bash -c "chmod 755 /usr/bin/find"

# writable_cron_script (vulnerability - file_permissions)
docker exec ctf-e2e-test bash -c "chmod 750 /opt/maintenance.sh"

# nopasswd_sudo (vulnerability - file_not_contains)
docker exec ctf-e2e-test bash -c "rm /etc/sudoers.d/backdoor"

# unauthorized_ssh_key (vulnerability - file_not_contains)
docker exec ctf-e2e-test bash -c "sed -i '/rogue@backdoor/d' /root/.ssh/authorized_keys"

# disable_ssh_root_login (hardening - file_contains)
docker exec ctf-e2e-test bash -c "sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config"

# change_root_password (hardening - password_changed)
# Uses ROOT_PASSWORD from .env.test as the known default, change to anything else
docker exec ctf-e2e-test bash -c "echo 'root:N3wTestP@ssw0rd!' | chpasswd"

# setup_ssh_key_auth (hardening - file_contains)
docker exec ctf-e2e-test bash -c "sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config"

# install_fail2ban (hardening - service_running)
docker exec ctf-e2e-test bash -c "apt-get update -qq && apt-get install -y -qq fail2ban > /dev/null 2>&1 && systemctl start fail2ban && systemctl enable fail2ban"
```

## Step 6: Submit Fixed State — All Should Pass

```bash
PAYLOAD=$(docker exec ctf-e2e-test python3 /opt/ctf/collect.py)
curl -s -b /tmp/ctf-test-cookies.txt -X POST http://localhost:8000/api/verify \
  -H "Content-Type: application/json" -d "$PAYLOAD" | python3 -m json.tool
```

**Expected:** Every module returns `"passed": true` with correct points. Total should equal sum of all module points.

| Module | Points | Verification Type |
|--------|--------|-------------------|
| `world_writable_shadow` | 100 | `file_permissions` |
| `suid_find` | 100 | `file_permissions` |
| `writable_cron_script` | 200 | `file_permissions` |
| `nopasswd_sudo` | 200 | `file_not_contains` |
| `unauthorized_ssh_key` | 300 | `file_not_contains` |
| `disable_ssh_root_login` | 100 | `file_contains` |
| `change_root_password` | 100 | `password_changed` |
| `install_fail2ban` | 200 | `service_running` |
| `setup_ssh_key_auth` | 200 | `file_contains` |
| **Total** | **1500** | |

## Step 7: Verify Idempotency — No Double Points

```bash
PAYLOAD=$(docker exec ctf-e2e-test python3 /opt/ctf/collect.py)
curl -s -b /tmp/ctf-test-cookies.txt -X POST http://localhost:8000/api/verify \
  -H "Content-Type: application/json" -d "$PAYLOAD" | python3 -m json.tool
```

**Expected:** All modules still `"passed": true` but `"points_awarded": 0` (already claimed). `"total_points"` unchanged.

## Cleanup

```bash
docker rm -f ctf-e2e-test
docker compose down
rm -f ctf.db
# Restore production .env if needed
```

## Adding New Modules

When adding a new module, update this test plan:
1. Add the fix command to Step 5
2. Add a row to the expected results table in Step 6
3. Update the total points
4. If using a new verification type, document what the unfixed and fixed collected data should look like
