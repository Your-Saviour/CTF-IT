# VM Onboarding — Problem Statement

## Context

The CTF platform supports VM-based deployments alongside the existing per-user Docker flow. VMs are team-scoped and registered manually by admins. Once a VM is registered, the admin must apply vulnerabilities to it (via Ansible) and configure Caldera to run red team emulation against it.

Currently this requires a sequence of manual steps split across three machines. This document describes the problem so automated features can be built.

---

## Current Flow (Manual — Too Much for an Admin)

### Step 1: Generate exports from the API (on the server, via SSH)
```bash
docker exec ctf-api python3 -c "
import requests
s = requests.Session()
s.post('http://localhost:8000/auth/login', data={'username': 'admin', 'password': 'Admin2026!'})
r = s.post('http://localhost:8000/admin/ansible-export', json={'event_id': 1})
open('/tmp/ansible_export.zip', 'wb').write(r.content)
"
docker cp ctf-api:/tmp/ansible_export.zip /opt/ansible_export.zip
mkdir -p /opt/ansible_export && cd /opt/ansible_export && unzip -o /opt/ansible_export.zip
```

### Step 2: Prepare the target VM (on the server, SSH into the target)
```bash
ssh root@<TARGET_IP> "apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-flask sqlite3"
```

### Step 3: Create an Ansible inventory and run the playbook (on the server)
```bash
cat > /opt/ansible_export/inventory << EOF
[targets]
<TARGET_IP> ansible_user=root ansible_ssh_private_key_file=<key> ansible_ssh_common_args='-o StrictHostKeyChecking=no'
EOF

cd /opt/ansible_export
ansible-playbook -i inventory playbook.yml --become
```

### Step 4: Run the Caldera setup (on the server)
```bash
docker exec ctf-api python3 -c "
import requests, json
s = requests.Session()
s.post('http://localhost:8000/auth/login', data={'username': 'admin', 'password': 'Admin2026!'})
r = s.post('http://localhost:8000/admin/caldera-setup', json={'event_id': 1})
print(json.dumps(r.json(), indent=2))
"
```

### Step 5: Deploy the Sandcat agent (on the target VM)
```bash
SERVER="http://<SERVER_IP>:8888"
cd /tmp
curl -s -o sandcat "$SERVER/file/download" -H "platform: linux" -H "file: sandcat.go" --max-time 120
chmod +x sandcat
nohup ./sandcat -server $SERVER -v > /tmp/sandcat.log 2>&1 &
```

---

## The Problem

- Steps 1–4 require SSH access to the server and running raw Docker exec commands
- Step 5 requires the admin to separately SSH into every target VM
- None of this is exposed in the admin UI
- There is no status feedback — the admin has no way to know if Ansible succeeded, if the agent checked in, or if the operation is running without checking logs manually
- The flow is not repeatable without copy-pasting commands
- There is no record in the database of what was applied to which VM or whether it succeeded

---

## What Should Exist Instead

A single button (or API call) on the VM detail page in the admin UI that handles the full lifecycle:

### `POST /admin/vms/{id}/provision`

Triggers a server-side job that:
1. Generates the Ansible export for the VM's assigned modules
2. SSHes into the target VM using the stored `ip_address`, `ssh_port`, `ssh_user`, and an SSH key managed by the platform
3. Installs prerequisites on the target (python3, pip, flask, sqlite3)
4. Runs the Ansible playbook against the target
5. Downloads the Sandcat agent binary from Caldera and deploys + starts it on the target
6. Updates the VM `status` field in the database (`registered` → `active`)
7. Returns live status/logs so the admin can see progress

### SSH Key Management

The platform needs a way to store or generate an SSH key per VM (or globally for the deploy server). The admin should be able to:
- Paste an existing private key when registering a VM, OR
- Have the platform generate a key and display the public key to add to the target

This key is used for both the Ansible run and the Sandcat deploy step.

### Agent Status

After provisioning, the VM detail page should show whether the Caldera agent has checked in (polling `GET /api/v2/agents` from the Caldera API and matching by IP).

---

## Relevant Existing Code

| File | Purpose |
|------|---------|
| `api/routes/vm.py` | VM CRUD, module assignment, VM-scoped Ansible export |
| `api/models.py` | `VM` model — has `ip_address`, `ssh_port`, `ssh_user`, `status`, `notes` |
| `builder/ansible.py` | `generate_ansible_export()` — generates playbook zip from modules |
| `deploy/caldera/config/local.yml` | Caldera config — `api_key_red` is the key for API calls |
| `frontend/templates/vm_detail.html` | VM detail page — where the "Provision" button should live |
| `deploy/docker-compose.yml` | Caldera is at `ctf-caldera:8888` internally |

## Key Notes for Implementation

- The deploy server runs inside Docker — SSH to the target must originate from the `ctf-api` container or a sidecar with the SSH key mounted
- Ansible is not currently installed in the `ctf-api` container — either install it in the Dockerfile or run it via a separate container/subprocess
- The Caldera API key is in `local.yml` (`api_key_red`) — the API endpoint is `http://ctf-caldera:8888` from within the Docker network
- Sandcat download endpoint: `GET http://ctf-caldera:8888/file/download` with headers `platform: linux` and `file: sandcat.go`
- The `caldera-setup` endpoint (`POST /admin/caldera-setup`) already handles generating the CTF plugin and creating the adversary operation — it should be called before or as part of provisioning
