# Firewall & VPC Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-team OPNsense firewall VMs and Vultr VPCs to CTF events via a new `role: "firewall"` vm_quota entry.

**Architecture:** Each team gets a dedicated Vultr VPC (10.{T}.1.0/24) and an OPNsense VM (bootstrapped from FreeBSD) acting as the LAN gateway at 10.{T}.1.1. Target VMs get VPC IPs alongside their public IPs; firewall VMs are created first (phases) so the gateway is up before targets connect. Attacker VMs remain public-only.

**Tech Stack:** Python/FastAPI, SQLAlchemy, Ansible via Semaphore, Vultr API (httpx), bcrypt (already in requirements.txt), OPNsense 25.1

---

## File Map

**Create:**
- `bases/opnsense/opnsense.yaml` — base type definition for OPNsense (FreeBSD 14, vc2-2c-4gb)
- `tests/test_vm_quota_validation.py` — tests for vm_quota role validation
- `playbooks/create-firewall.yml` — create Vultr FreeBSD instance attached to VPC
- `playbooks/bootstrap-opnsense.yml` — convert FreeBSD → OPNsense, output admin password
- `playbooks/configure-vpc-interface.yml` — configure VPC netplan on Ubuntu target VMs
- `templates/opnsense_config.xml.j2` — OPNsense config.xml (LAN/WAN, SSH, HTTPS, NAT)
- `templates/vpc-netplan.yaml.j2` — Netplan static VPC config for target VMs

**Modify:**
- `builder/vm_quota_validation.py` — add `"firewall"` to `VALID_ROLES`
- `api/models.py` — add `vpc_id`/`team_index` to `Team`; `vpc_ip`/`admin_password` to `VM`
- `api/main.py` — add startup migration for new columns
- `playbooks/create-vm.yml` — optional `vpc_description` param for VPC attachment
- `api/routes/vm.py` — add `_create_team_vpc()`, `_run_firewall_create()`, `_run_configure_vpc_interface()`; modify `_provision_event_vms()` for phased flow; modify `_run_provision()` to chain VPC interface config

---

### Task 1: OPNsense base type + firewall role validation

**Files:**
- Create: `bases/opnsense/opnsense.yaml`
- Create: `tests/test_vm_quota_validation.py`
- Modify: `builder/vm_quota_validation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_vm_quota_validation.py`:

```python
import pytest
from builder.vm_quota_validation import validate_vm_quota


class TestFirewallRole:
    def test_firewall_role_is_valid(self):
        errors = validate_vm_quota(
            {
                "fw": {
                    "base_type": "opnsense",
                    "count": 1,
                    "role": "firewall",
                    "default_plan": "vc2-2c-4gb",
                }
            },
            valid_base_ids={"opnsense"},
        )
        assert errors == []

    def test_target_role_still_valid(self):
        errors = validate_vm_quota(
            {"t": {"base_type": "ubuntu_24_server", "count": 2, "role": "target"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert errors == []

    def test_attacker_role_still_valid(self):
        errors = validate_vm_quota(
            {"a": {"base_type": "ubuntu_24_server", "count": 1, "role": "attacker"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert errors == []

    def test_unknown_role_rejected(self):
        errors = validate_vm_quota(
            {"bad": {"base_type": "ubuntu_24_server", "count": 1, "role": "gateway"}},
            valid_base_ids={"ubuntu_24_server"},
        )
        assert any("role" in e for e in errors)

    def test_mixed_quota_with_firewall_valid(self):
        errors = validate_vm_quota(
            {
                "fw": {"base_type": "opnsense", "count": 1, "role": "firewall"},
                "target": {"base_type": "ubuntu_24_server", "count": 3, "role": "target"},
                "attacker": {"base_type": "ubuntu_24_server", "count": 1, "role": "attacker"},
            },
            valid_base_ids={"opnsense", "ubuntu_24_server"},
        )
        assert errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_vm_quota_validation.py -v
```
Expected: FAIL — `firewall_role_is_valid` and `mixed_quota_with_firewall_valid` fail with "role must be one of"

- [ ] **Step 3: Add "firewall" to VALID_ROLES**

In `builder/vm_quota_validation.py`, change line 4:

```python
VALID_ROLES = {"target", "attacker", "firewall"}
```

- [ ] **Step 4: Create OPNsense base type**

Create directory and file `bases/opnsense/opnsense.yaml`:

```yaml
id: opnsense
name: OPNsense Firewall
description: OPNsense firewall/router bootstrapped from FreeBSD 14 on Vultr. Used for per-team firewall VMs in CTF events. Do not use for regular target/attacker VMs.
os: "FreeBSD 14 x64"
default_plan: vc2-2c-4gb
icon: router
packages: []
steps: []
disabled: false
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_vm_quota_validation.py -v
```
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add bases/opnsense/opnsense.yaml tests/test_vm_quota_validation.py builder/vm_quota_validation.py
git commit -m "feat: add firewall role to vm_quota and opnsense base type"
```

---

### Task 2: Database model changes + migration

**Files:**
- Modify: `api/models.py`
- Modify: `api/main.py`

- [ ] **Step 1: Add new columns to Team and VM models**

In `api/models.py`, add to the `Team` class after `created_at`:

```python
    # VPC networking (set when event has a firewall role in vm_quota)
    vpc_id: Mapped[str] = mapped_column(String(64), nullable=True)
    team_index: Mapped[int] = mapped_column(Integer, nullable=True)
```

Add to the `VM` class after `base_type`:

```python
    # VPC networking
    vpc_ip: Mapped[str] = mapped_column(String(45), nullable=True)
    # OPNsense admin password (firewall VMs only)
    admin_password: Mapped[str] = mapped_column(String(128), nullable=True)
```

- [ ] **Step 2: Add startup migration in api/main.py**

In `api/main.py`, inside the lifespan function, add after the existing `if inspector.has_table("vms"):` block (around line 41):

```python
        # New VPC columns on vms
        if inspector.has_table("vms"):
            existing = {col["name"] for col in inspector.get_columns("vms")}
            for col, typ in {
                "vpc_ip": "VARCHAR(45)",
                "admin_password": "VARCHAR(128)",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE vms ADD COLUMN {col} {typ}"))

        if inspector.has_table("teams"):
            existing = {col["name"] for col in inspector.get_columns("teams")}
            for col, typ in {
                "vpc_id": "VARCHAR(64)",
                "team_index": "INTEGER",
            }.items():
                if col not in existing:
                    db.execute(text(f"ALTER TABLE teams ADD COLUMN {col} {typ}"))
```

Note: add this AFTER the existing `vms` migration block (which handles provision_step, vultr fields etc.), not inside it.

- [ ] **Step 3: Verify the app starts cleanly**

```bash
python -m pytest tests/ -v
```
Expected: All existing tests still PASS (model changes are additive/nullable)

- [ ] **Step 4: Commit**

```bash
git add api/models.py api/main.py
git commit -m "feat: add vpc_id/team_index to Team, vpc_ip/admin_password to VM"
```

---

### Task 3: OPNsense config.xml template + VPC netplan template

**Files:**
- Create: `templates/opnsense_config.xml.j2`
- Create: `templates/vpc-netplan.yaml.j2`

- [ ] **Step 1: Create OPNsense config.xml template**

Create `templates/opnsense_config.xml.j2`:

```xml
<?xml version="1.0"?>
<opnsense>
  <theme>opnsense</theme>
  <sysctl>
    <item>
      <descr>Increase UFS read-ahead speeds</descr>
      <tunable>vfs.read_max</tunable>
      <value>default</value>
    </item>
    <item>
      <descr>Enable TCP keepalives</descr>
      <tunable>net.inet.tcp.always_keepalive</tunable>
      <value>1</value>
    </item>
  </sysctl>
  <system>
    <optimization>normal</optimization>
    <hostname>{{ opnsense_hostname }}</hostname>
    <domain>localdomain</domain>
    <dnsserver>1.1.1.1</dnsserver>
    <dnsserver>8.8.8.8</dnsserver>
    <group>
      <name>admins</name>
      <description>System Administrators</description>
      <scope>system</scope>
      <gid>1999</gid>
      <member>0</member>
      <priv>page-all</priv>
    </group>
    <user>
      <name>root</name>
      <descr>System Administrator</descr>
      <scope>system</scope>
      <groupname>admins</groupname>
      <password>{{ opnsense_admin_password_hash }}</password>
      <uid>0</uid>
      <authorizedkeys>{{ opnsense_ssh_pubkey | b64encode }}</authorizedkeys>
    </user>
    <nextuid>2000</nextuid>
    <nextgid>2000</nextgid>
    <timezone>UTC</timezone>
    <language>en_US</language>
    <disablecheck>1</disablecheck>
    <ssh>
      <enabled>enabled</enabled>
      <port>22</port>
      <permitrootlogin>1</permitrootlogin>
      <passwordauth>1</passwordauth>
    </ssh>
    <webgui>
      <protocol>https</protocol>
      <port>443</port>
      <ssl-certref>self-signed</ssl-certref>
      <nohttpreferercheck>1</nohttpreferercheck>
      <noantilockout>1</noantilockout>
    </webgui>
  </system>
  <interfaces>
    <wan>
      <if>vtnet0</if>
      <descr>WAN</descr>
      <enable>1</enable>
      <spoofmac/>
      <ipaddr>dhcp</ipaddr>
      <dhcphostname/>
      <alias-address/>
      <alias-subnet>32</alias-subnet>
      <gateway/>
    </wan>
    <lan>
      <if>vtnet1</if>
      <descr>LAN</descr>
      <enable>1</enable>
      <spoofmac/>
      <ipaddr>{{ opnsense_lan_ip }}</ipaddr>
      <subnet>{{ opnsense_lan_subnet }}</subnet>
    </lan>
  </interfaces>
  <gateways/>
  <staticroutes/>
  <filter>
    <rule>
      <type>pass</type>
      <interface>wan</interface>
      <ipprotocol>inet46</ipprotocol>
      <statetype>keep state</statetype>
      <direction>in</direction>
      <quick>1</quick>
      <protocol>tcp</protocol>
      <source><any>1</any></source>
      <destination>
        <network>(self)</network>
        <port>443</port>
      </destination>
      <descr>Allow HTTPS to web UI</descr>
    </rule>
    <rule>
      <type>pass</type>
      <interface>wan</interface>
      <ipprotocol>inet46</ipprotocol>
      <statetype>keep state</statetype>
      <direction>in</direction>
      <quick>1</quick>
      <protocol>tcp</protocol>
      <source><any>1</any></source>
      <destination>
        <network>(self)</network>
        <port>22</port>
      </destination>
      <descr>Allow SSH access</descr>
    </rule>
    <rule>
      <type>pass</type>
      <interface>wan</interface>
      <ipprotocol>inet46</ipprotocol>
      <statetype>keep state</statetype>
      <direction>out</direction>
      <quick>1</quick>
      <source><any>1</any></source>
      <destination><any>1</any></destination>
      <descr>Allow all outbound traffic</descr>
    </rule>
    <rule>
      <type>pass</type>
      <interface>lan</interface>
      <ipprotocol>inet46</ipprotocol>
      <statetype>keep state</statetype>
      <direction>in</direction>
      <quick>1</quick>
      <source><any>1</any></source>
      <destination><any>1</any></destination>
      <descr>Allow all LAN traffic</descr>
    </rule>
  </filter>
  <nat>
    <outbound>
      <mode>automatic</mode>
    </outbound>
  </nat>
  <dhcpd/>
  <snmpd>
    <syslocation/>
    <syscontact/>
    <rocommunity>public</rocommunity>
  </snmpd>
  <syslog>
    <reverse/>
  </syslog>
</opnsense>
```

- [ ] **Step 2: Create VPC netplan template**

Create `templates/vpc-netplan.yaml.j2`:

```yaml
# VPC interface configuration — managed by CTF platform
network:
  version: 2
  ethernets:
    {{ vpc_interface }}:
      mtu: 1450
      addresses:
        - {{ vpc_ip }}/{{ vpc_subnet_mask }}
      routes:
        - to: 10.0.0.0/8
          via: {{ vpc_gateway }}
```

- [ ] **Step 3: Commit**

```bash
git add templates/opnsense_config.xml.j2 templates/vpc-netplan.yaml.j2
git commit -m "feat: add OPNsense config.xml and VPC netplan templates"
```

---

### Task 4: Firewall and bootstrap playbooks

**Files:**
- Create: `playbooks/create-firewall.yml`
- Create: `playbooks/bootstrap-opnsense.yml`

- [ ] **Step 1: Create create-firewall.yml**

Create `playbooks/create-firewall.yml`:

```yaml
---
# Create a Vultr FreeBSD instance attached to a VPC, for OPNsense bootstrapping.
#
# Required extra vars:
#   vm_hostname       - instance label and hostname
#   vm_plan           - Vultr plan ID (e.g. "vc2-2c-4gb")
#   vm_region         - Vultr region code (e.g. "ewr")
#   ssh_key_name      - SSH key name registered in Vultr
#   ssh_public_key    - Ed25519 public key content
#   vultr_api_key     - Vultr API key
#   vpc_description   - VPC description string (e.g. "ctf-event-1-team-1")
#
# Optional extra vars:
#   domain_name         - Domain for Cloudflare DNS A record
#   cloudflare_api_key  - Cloudflare API token

- name: Create Vultr Firewall VM
  hosts: localhost
  connection: local
  gather_facts: false

  environment:
    VULTR_API_KEY: "{{ vultr_api_key }}"

  tasks:

    - name: Register SSH public key with Vultr (idempotent)
      vultr.cloud.ssh_key:
        name: "{{ ssh_key_name }}"
        ssh_key: "{{ ssh_public_key }}"
        state: present

    - name: Create Vultr FreeBSD instance with VPC
      vultr.cloud.instance:
        label: "{{ vm_hostname }}"
        hostname: "{{ vm_hostname }}"
        plan: "{{ vm_plan }}"
        os: "FreeBSD 14 x64"
        region: "{{ vm_region }}"
        ssh_keys:
          - "{{ ssh_key_name }}"
        vpcs:
          - "{{ vpc_description }}"
        ddos_protection: false
        backups: false
        enable_ipv6: false
        state: present
      register: vultr_instance

    - name: Create Cloudflare DNS A record
      community.general.cloudflare_dns:
        zone: "{{ domain_name }}"
        record: "{{ vm_hostname }}"
        type: A
        value: "{{ vultr_instance.vultr_instance.main_ip }}"
        api_token: "{{ cloudflare_api_key }}"
        solo: true
      register: dns_result
      when:
        - domain_name is defined
        - domain_name | length > 0
        - cloudflare_api_key is defined
        - cloudflare_api_key | length > 0

    - name: Output instance details
      ansible.builtin.debug:
        msg: >-
          VULTR_RESULT={{ {
            'ip': vultr_instance.vultr_instance.main_ip,
            'vultr_id': vultr_instance.vultr_instance.id,
            'dns_record_id': (dns_result.result.id | default('')) if dns_result is not skipped else ''
          } | to_json }}
```

- [ ] **Step 2: Create bootstrap-opnsense.yml**

Create `playbooks/bootstrap-opnsense.yml`:

```yaml
---
# Bootstrap FreeBSD 14 into OPNsense 25.1.
# Expects a pre-computed admin password hash and SSH public key as extra vars.
# The config.xml is deployed BEFORE bootstrapping so OPNsense boots with SSH enabled.
#
# Required extra vars:
#   opnsense_hostname           - hostname to set in config.xml
#   opnsense_lan_ip             - LAN IP (e.g. "10.1.1.1")
#   opnsense_lan_subnet         - LAN subnet mask (e.g. 24)
#   opnsense_admin_password_hash - bcrypt hash of admin password
#   opnsense_ssh_pubkey         - SSH public key content (for authorized_keys)
#   opnsense_release            - OPNsense release version (e.g. "25.1")

- name: Bootstrap and Configure OPNsense
  hosts: all
  become: true
  gather_facts: true

  tasks:
    # =====================================================
    # Phase 1: Deploy config BEFORE bootstrap
    # =====================================================

    - name: Verify host is running FreeBSD
      ansible.builtin.assert:
        that:
          - ansible_os_family == "FreeBSD"
        fail_msg: "Expected FreeBSD but got {{ ansible_os_family }}. OPNsense bootstrap requires a FreeBSD base."

    - name: Ensure /conf directory exists
      ansible.builtin.file:
        path: /conf
        state: directory
        owner: root
        group: wheel
        mode: "0755"

    - name: Deploy OPNsense config.xml (before bootstrap)
      ansible.builtin.template:
        src: opnsense_config.xml.j2
        dest: /conf/config.xml
        owner: root
        group: wheel
        mode: "0644"

    - name: Ensure SSH authorized_keys directory exists
      ansible.builtin.file:
        path: /root/.ssh
        state: directory
        owner: root
        group: wheel
        mode: "0700"

    - name: Deploy SSH authorized_keys
      ansible.builtin.copy:
        content: "{{ opnsense_ssh_pubkey }}\n"
        dest: /root/.ssh/authorized_keys
        owner: root
        group: wheel
        mode: "0600"

    # =====================================================
    # Phase 2: Bootstrap FreeBSD → OPNsense
    # =====================================================

    - name: Download opnsense-bootstrap.sh
      ansible.builtin.get_url:
        url: "https://raw.githubusercontent.com/opnsense/update/master/src/bootstrap/opnsense-bootstrap.sh.in"
        dest: /tmp/opnsense-bootstrap.sh
        mode: "0755"

    - name: Run OPNsense bootstrap (installs packages and reboots)
      ansible.builtin.shell:
        cmd: "/tmp/opnsense-bootstrap.sh -r {{ opnsense_release }} -y"
      async: 600
      poll: 10
      ignore_errors: true
      ignore_unreachable: true

    # =====================================================
    # Phase 3: Reconnect after reboot
    # =====================================================

    - name: Clear known_hosts entry (host key changes after OPNsense install)
      ansible.builtin.known_hosts:
        name: "{{ ansible_host }}"
        state: absent
      delegate_to: localhost
      become: false

    - name: Wait for SSH to come back (OPNsense booted)
      ansible.builtin.wait_for:
        host: "{{ ansible_host }}"
        port: 22
        state: started
        timeout: 600
        delay: 60
      delegate_to: localhost
      become: false

    - name: Pause for OPNsense services to initialize
      ansible.builtin.pause:
        seconds: 30

    - name: Re-gather facts on OPNsense host
      ansible.builtin.setup:
        gather_subset: min

    # =====================================================
    # Phase 4: Post-boot configuration
    # =====================================================

    - name: Reload OPNsense configuration
      ansible.builtin.shell:
        cmd: "configctl service reload all"
      changed_when: true

    - name: Restart OPNsense web UI
      ansible.builtin.shell:
        cmd: "configctl webgui restart"
      changed_when: true

    - name: Signal bootstrap complete
      ansible.builtin.debug:
        msg: "OPNSENSE_BOOTSTRAP_OK=true"
```

- [ ] **Step 3: Commit**

```bash
git add playbooks/create-firewall.yml playbooks/bootstrap-opnsense.yml
git commit -m "feat: add create-firewall and bootstrap-opnsense playbooks"
```

---

### Task 5: VPC interface playbook + update create-vm.yml

**Files:**
- Create: `playbooks/configure-vpc-interface.yml`
- Modify: `playbooks/create-vm.yml`

- [ ] **Step 1: Create configure-vpc-interface.yml**

Create `playbooks/configure-vpc-interface.yml`:

```yaml
---
# Configure the Vultr VPC network interface on an Ubuntu target VM.
# The VPC interface (ens7) is assigned a static IP with MTU 1450 (required for Vultr VXLAN).
#
# Required extra vars:
#   vpc_ip          - Static IP to assign (e.g. "10.1.1.10")
#   vpc_subnet_mask - Subnet mask bits (e.g. 24)
#   vpc_gateway     - Default gateway on VPC (OPNsense LAN IP, e.g. "10.1.1.1")
#   vpc_interface   - Interface name for the VPC NIC (default: "ens7" on Vultr Ubuntu)

- name: Configure VPC Interface
  hosts: all
  become: true
  gather_facts: false

  tasks:

    - name: Ensure netplan config directory exists
      ansible.builtin.file:
        path: /etc/netplan
        state: directory
        mode: "0755"

    - name: Deploy VPC netplan config
      ansible.builtin.template:
        src: vpc-netplan.yaml.j2
        dest: /etc/netplan/99-vpc.yaml
        owner: root
        group: root
        mode: "0600"
      vars:
        vpc_interface: "{{ vpc_interface | default('ens7') }}"

    - name: Apply netplan configuration
      ansible.builtin.shell:
        cmd: netplan apply
      changed_when: true
```

- [ ] **Step 2: Add optional vpc_description to create-vm.yml**

In `playbooks/create-vm.yml`, replace the "Create Vultr instance" task:

```yaml
    - name: Create Vultr instance
      vultr.cloud.instance:
        label: "{{ vm_hostname }}"
        hostname: "{{ vm_hostname }}"
        plan: "{{ vm_plan }}"
        os: "{{ vm_os }}"
        region: "{{ vm_region }}"
        ssh_keys:
          - "{{ ssh_key_name }}"
        vpcs: "{{ [vpc_description] if vpc_description is defined and vpc_description else omit }}"
        ddos_protection: false
        backups: false
        enable_ipv6: false
        state: present
      register: vultr_instance
```

- [ ] **Step 3: Commit**

```bash
git add playbooks/configure-vpc-interface.yml playbooks/create-vm.yml
git commit -m "feat: add configure-vpc-interface playbook, add optional vpc_description to create-vm"
```

---

### Task 6: VPC creation helper (_create_team_vpc)

**Files:**
- Modify: `api/routes/vm.py`

This adds a synchronous helper that calls the Vultr VPC API directly (no Semaphore needed for a single API call).

- [ ] **Step 1: Add TEMPLATES_DIR constant and _create_team_vpc function**

In `api/routes/vm.py`, after the existing `PLAYBOOKS_DIR` constant (around line 33), add:

```python
TEMPLATES_DIR = _HERE / "templates"
```

Then add the `_create_team_vpc` function after `_get_or_create_vultr_semaphore_project` (around line 1147):

```python
# ── VPC Creation ───────────────────────────────────────────────────────────────

def _create_team_vpc(team_id: int, event_id: int, region: str) -> None:
    """Create a Vultr VPC for a team and store the VPC ID on the team record.

    Uses the Vultr REST API directly (no Semaphore needed for a single API call).
    VPC description format: "ctf-event-{event_id}-team-{team_index}"
    Subnet format: "10.{team_index}.1.0/24"
    """
    import httpx as _httpx

    from api.database import SessionLocal

    db = SessionLocal()
    try:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team or team.team_index is None:
            _log.error("_create_team_vpc: team %d not found or missing team_index", team_id)
            return

        vpc_description = f"ctf-event-{event_id}-team-{team.team_index}"
        v4_subnet = f"10.{team.team_index}.1.0"

        _log.info(
            "Creating VPC '%s' (%s/24) in region %s for team %d",
            vpc_description, v4_subnet, region, team_id,
        )

        resp = _httpx.post(
            "https://api.vultr.com/v2/vpcs",
            headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
            json={
                "region": region,
                "v4_subnet": v4_subnet,
                "v4_subnet_mask": 24,
                "description": vpc_description,
            },
            timeout=30.0,
        )
        resp.raise_for_status()

        vpc_id = resp.json()["vpc"]["id"]
        team.vpc_id = vpc_id
        db.commit()

        _log.info("VPC '%s' created with ID %s", vpc_description, vpc_id)

    except Exception as exc:
        _log.exception("Failed to create VPC for team %d: %s", team_id, exc)
        raise
    finally:
        db.close()
```

- [ ] **Step 2: Verify the module still imports cleanly**

```bash
python -c "from api.routes.vm import _create_team_vpc; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/routes/vm.py
git commit -m "feat: add _create_team_vpc helper using Vultr REST API"
```

---

### Task 7: _run_firewall_create function

**Files:**
- Modify: `api/routes/vm.py`

This is the longest task. It creates the OPNsense VM, bootstraps it, and stores credentials.

- [ ] **Step 1: Add _run_firewall_create function**

Add the following function in `api/routes/vm.py` after `_create_team_vpc` (around line 1185):

```python
# ── OPNsense Firewall Provisioning ─────────────────────────────────────────────

def _run_firewall_create(vm_id: int) -> None:
    """Synchronous background task: create a Vultr FreeBSD VM, attach it to the team VPC,
    then bootstrap it into OPNsense.

    Stores admin password on vm.admin_password.
    Sets vm.status = "active" on success, "failed" on error.
    """
    import re as _re
    import shutil as _shutil

    import bcrypt as _bcrypt

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return
        team = db.query(Team).filter(Team.id == vm.team_id).first()
        if not team:
            raise RuntimeError(f"Team {vm.team_id} not found for firewall VM {vm_id}")

        # ── Stage 1: Create Vultr VM via create-firewall.yml ────────────────
        _update_provision_step(db, vm, "staging_playbook")

        export_id = f"firewall_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(PLAYBOOKS_DIR / "create-firewall.yml", playbook_dir / "create-firewall.yml")
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        _update_provision_step(db, vm, "configuring_semaphore")

        private_key, public_key = get_or_create_platform_keypair(db)

        vpc_description = f"ctf-event-{vm.event_id}-team-{team.team_index}"

        extra_vars: dict = {
            "vm_hostname": vm.hostname or f"ctf-fw-{vm_id}",
            "vm_plan": vm.vultr_plan or "vc2-2c-4gb",
            "vm_region": vm.vultr_region or VULTR_DEFAULT_REGION,
            "ssh_key_name": "ctf-platform",
            "ssh_public_key": public_key,
            "vultr_api_key": VULTR_API_KEY,
            "vpc_description": vpc_description,
        }
        if CLOUDFLARE_API_TOKEN and CLOUDFLARE_DOMAIN:
            extra_vars["cloudflare_api_key"] = CLOUDFLARE_API_TOKEN
            extra_vars["domain_name"] = CLOUDFLARE_DOMAIN

        _update_provision_step(db, vm, "creating_instance")

        with SemaphoreClient() as client:
            client.login()
            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            inv_id = client.create_localhost_inventory(project_id, f"localhost-fw-{vm_id}", key_id)
            repo_id = client.create_repository(
                project_id, f"create-fw-{vm_id}", str(playbook_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"create-fw-{vm_id}", "create-firewall.yml",
                inv_id, repo_id, key_id, extra_vars=extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"create-firewall.yml failed:\n{tail}")
                time.sleep(10)

            output_lines = client.get_task_output(project_id, task_id)

        # Parse IP from output
        _update_provision_step(db, vm, "extracting_results")
        _ansi = _re.compile(r'\x1b\[[0-9;]*[mGKHF]')
        cleaned = " ".join(_ansi.sub('', line).strip() for line in output_lines)

        match = _re.search(r'VULTR_RESULT=(\{.*?\})', cleaned)
        if not match:
            raise RuntimeError("Could not extract firewall VM IP from playbook output")
        vultr_result = json.loads(match.group(1))
        if not vultr_result.get("ip"):
            raise RuntimeError("VULTR_RESULT missing ip field")

        vm.ip_address = vultr_result["ip"]
        vm.vultr_id = vultr_result.get("vultr_id", "")
        vm.cloudflare_record_id = vultr_result.get("dns_record_id") or None
        vm.status = "registered"
        vm.provision_step = "bootstrapping_opnsense"
        vm.updated_at = utcnow()
        db.commit()

        # ── Stage 2: Bootstrap OPNsense ──────────────────────────────────────
        # Generate admin credentials in Python (no Ansible passlib dependency needed)
        import secrets as _secrets
        import string as _string
        admin_password = ''.join(
            _secrets.choice(_string.ascii_letters + _string.digits)
            for _ in range(20)
        )
        # OPNsense expects bcrypt $2y$ format; Python bcrypt produces $2b$ which OPNsense accepts
        password_hash = _bcrypt.hashpw(
            admin_password.encode(), _bcrypt.gensalt(rounds=10)
        ).decode()

        bootstrap_dir = playbook_dir / "bootstrap"
        bootstrap_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "bootstrap-opnsense.yml",
            bootstrap_dir / "bootstrap-opnsense.yml",
        )
        # Stage templates/ subdirectory so Ansible finds opnsense_config.xml.j2
        bootstrap_templates_dir = bootstrap_dir / "templates"
        bootstrap_templates_dir.mkdir(exist_ok=True)
        _shutil.copy(
            TEMPLATES_DIR / "opnsense_config.xml.j2",
            bootstrap_templates_dir / "opnsense_config.xml.j2",
        )
        bootstrap_collections_dir = bootstrap_dir / "collections"
        bootstrap_collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            bootstrap_collections_dir / "requirements.yml",
        )

        team_index = team.team_index or 1
        bootstrap_extra_vars = {
            "opnsense_hostname": vm.hostname or f"ctf-fw-{vm_id}",
            "opnsense_lan_ip": f"10.{team_index}.1.1",
            "opnsense_lan_subnet": 24,
            "opnsense_admin_password_hash": password_hash,
            "opnsense_ssh_pubkey": public_key,
            "opnsense_release": "25.1",
        }

        firewall_ip = vm.ip_address
        with SemaphoreClient() as client:
            client.login()
            project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            # Remote inventory pointing to the firewall's public IP
            fw_inv_id = client.create_inventory(
                project_id, f"fw-host-{vm_id}", firewall_ip,
                ssh_user="root", ssh_port=22, key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id, f"bootstrap-fw-{vm_id}", str(bootstrap_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"bootstrap-fw-{vm_id}", "bootstrap-opnsense.yml",
                fw_inv_id, repo_id, key_id, extra_vars=bootstrap_extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            # Bootstrap takes 10-15 minutes; poll patiently
            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    raise RuntimeError(f"bootstrap-opnsense.yml failed:\n{tail}")
                time.sleep(15)

        # Store credentials and mark active
        vm.admin_password = admin_password
        vm.vpc_ip = f"10.{team_index}.1.1"
        vm.status = "active"
        vm.provision_step = "completed"
        vm.updated_at = utcnow()
        db.commit()

        _log.info("Firewall VM %d (%s) provisioned and active at %s", vm_id, vm.hostname, vm.ip_address)

    except Exception as exc:
        from api.models import utcnow as _utcnow
        _log.exception("Firewall VM creation failed for VM %d", vm_id)
        try:
            vm.status = "failed"
            vm.provision_step = "failed"
            vm.provision_error = str(exc)
            vm.updated_at = _utcnow()
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)
```

- [ ] **Step 2: Verify import still works**

```bash
python -c "from api.routes.vm import _run_firewall_create; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/routes/vm.py
git commit -m "feat: add _run_firewall_create for OPNsense bootstrapping"
```

---

### Task 8: VPC interface configuration + phased _provision_event_vms

**Files:**
- Modify: `api/routes/vm.py`

Two changes here: add `_run_configure_vpc_interface()`, chain it from `_run_provision()`, and rework `_provision_event_vms()` for phased firewall-first ordering.

- [ ] **Step 1: Add _run_configure_vpc_interface function**

Add after `_run_firewall_create` in `api/routes/vm.py`:

```python
# ── VPC Interface Configuration ───────────────────────────────────────────────

def _run_configure_vpc_interface(vm_id: int) -> None:
    """Configure the VPC network interface on a target Ubuntu VM after provisioning.

    Deploys a netplan config for the VPC secondary NIC (ens7 on Vultr), assigns the
    static VPC IP (stored in vm.vpc_ip), and applies it.
    """
    import shutil as _shutil

    from api.database import SessionLocal
    from api.models import utcnow
    from api.services.semaphore import SemaphoreClient
    from api.services.ssh_keys import get_or_create_platform_keypair

    db = SessionLocal()
    playbook_dir = None
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm or not vm.vpc_ip or not vm.ip_address:
            return
        team = db.query(Team).filter(Team.id == vm.team_id).first()

        _update_provision_step(db, vm, "configuring_vpc_interface")

        export_id = f"vpcif_{vm_id}_{uuid.uuid4().hex[:8]}"
        playbook_dir = Path(SHARED_PLAYBOOK_DIR) / export_id
        playbook_dir.mkdir(parents=True, exist_ok=True)

        _shutil.copy(
            PLAYBOOKS_DIR / "configure-vpc-interface.yml",
            playbook_dir / "configure-vpc-interface.yml",
        )
        templates_dir = playbook_dir / "templates"
        templates_dir.mkdir(exist_ok=True)
        _shutil.copy(
            TEMPLATES_DIR / "vpc-netplan.yaml.j2",
            templates_dir / "vpc-netplan.yaml.j2",
        )
        collections_dir = playbook_dir / "collections"
        collections_dir.mkdir(exist_ok=True)
        _shutil.copy(
            PLAYBOOKS_DIR / "collections" / "requirements.yml",
            collections_dir / "requirements.yml",
        )

        private_key, _ = get_or_create_platform_keypair(db)
        team_index = team.team_index if team else 1
        vpc_gateway = f"10.{team_index}.1.1"

        extra_vars = {
            "vpc_ip": vm.vpc_ip,
            "vpc_subnet_mask": 24,
            "vpc_gateway": vpc_gateway,
            "vpc_interface": "ens7",
        }

        with SemaphoreClient() as client:
            client.login()
            project_id = vm.event.semaphore_project_id if vm.event and vm.event.semaphore_project_id else None
            if not project_id:
                project_id, key_id = _get_or_create_vultr_semaphore_project(db, client, private_key)
            else:
                key_id = vm.event.semaphore_key_id

            inv_id = client.create_inventory(
                project_id, f"vpcif-{vm_id}", vm.ip_address,
                ssh_user=vm.ssh_user or "root", ssh_port=vm.ssh_port or 22, key_id=key_id,
            )
            repo_id = client.create_repository(
                project_id, f"vpcif-{vm_id}", str(playbook_dir), key_id
            )
            tmpl_id = client.create_template(
                project_id, f"vpcif-{vm_id}", "configure-vpc-interface.yml",
                inv_id, repo_id, key_id, extra_vars=extra_vars,
            )
            task_id = client.run_task(project_id, tmpl_id)
            vm.semaphore_task_id = task_id
            vm.updated_at = utcnow()
            db.commit()

            while True:
                status = client.get_task_status(project_id, task_id)
                if status == "success":
                    break
                elif status in ("error", "stopped"):
                    output_lines = client.get_task_output(project_id, task_id)
                    tail = "\n".join(output_lines[-10:]) if output_lines else "(no output)"
                    _log.warning("VPC interface config failed for VM %d:\n%s", vm_id, tail)
                    # Non-fatal: VM is still usable via public IP
                    return
                time.sleep(5)

        _log.info("VPC interface configured on VM %d: %s via %s", vm_id, vm.vpc_ip, vpc_gateway)

    except Exception as exc:
        _log.warning("VPC interface configuration failed for VM %d (non-fatal): %s", vm_id, exc)
    finally:
        db.close()
        if playbook_dir and playbook_dir.exists():
            shutil.rmtree(playbook_dir, ignore_errors=True)
```

- [ ] **Step 2: Chain VPC interface config from _run_provision**

In `_run_provision`, find the section at the end that sets `vm.status = "active"` and `vm.provision_step = "completed"`. After `db.commit()` on those lines, add:

```python
        # If this VM has a VPC IP assigned, configure its VPC network interface
        if vm.vpc_ip:
            _run_configure_vpc_interface(vm_id)
```

The relevant section to find in `_run_provision` will look like:
```python
        vm.status = "active"
        vm.provision_step = "completed"
        vm.updated_at = utcnow()
        db.commit()
```

Add the `_run_configure_vpc_interface` call immediately after the `db.commit()`.

- [ ] **Step 3: Rework _provision_event_vms for phased flow**

Replace the body of `_provision_event_vms` with the version below. Key changes:
1. Detect `has_firewall` flag upfront
2. If yes: assign team_indexes, create VPCs, create all VM records, spawn firewall threads FIRST and join them, then spawn target/attacker threads
3. If no: original flat behavior (no sequencing needed)

Find `def _provision_event_vms(event_id: int) -> None:` and replace the full function:

```python
def _provision_event_vms(event_id: int) -> None:
    """Synchronous background task: create all VMs for an event based on vm_quota.

    If the quota contains a 'firewall' role, provisions in phases:
      Phase 1: Create Vultr VPCs (one per team, via REST API)
      Phase 2: Create and bootstrap OPNsense firewall VMs (threads, joined)
      Phase 3: Create target + attacker VMs (threads, not joined)

    Without a firewall role, all VMs are spawned concurrently as before.
    """
    import threading

    import httpx as _httpx

    from api.database import SessionLocal
    from api.models import utcnow

    from builder.base_loader import load_base_type
    from builder.module_loader import load_all_modules
    from builder.plan_sizing import plan_for_vm
    from builder.selector import select_modules

    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event or not event.vm_quota:
            return

        vm_quota = json.loads(event.vm_quota)
        module_quota = json.loads(event.quota)
        teams = db.query(Team).filter(Team.event_id == event_id).all()
        if not teams:
            _log.warning("No teams for event %d — skipping VM provisioning", event_id)
            return

        has_firewall = any(spec.get("role") == "firewall" for spec in vm_quota.values())

        # Pre-create Semaphore project for target VM provisioning
        has_targets = any(spec.get("role") == "target" for spec in vm_quota.values())
        if has_targets and not event.semaphore_project_id:
            from api.services.semaphore import SemaphoreClient
            from api.services.ssh_keys import get_or_create_platform_keypair

            private_key, _ = get_or_create_platform_keypair(db)
            with SemaphoreClient() as client:
                client.login()
                project_id = client.create_project(f"CTF Event {event.id}: {event.name}")
                key_id = client.create_key(project_id, "platform-key", private_key)
                event.semaphore_project_id = project_id
                event.semaphore_key_id = key_id
                db.commit()

        # Fetch Vultr plans once for plan sizing
        available_plans = []
        if VULTR_API_KEY:
            try:
                resp = _httpx.get(
                    "https://api.vultr.com/v2/plans",
                    headers={"Authorization": f"Bearer {VULTR_API_KEY}"},
                    params={"type": "vc2", "per_page": 500},
                    timeout=15.0,
                )
                resp.raise_for_status()
                available_plans = [
                    {"id": p["id"], "ram": p["ram"], "vcpu_count": p["vcpu_count"], "monthly_cost": p["monthly_cost"]}
                    for p in resp.json().get("plans", [])
                    if p.get("id", "").startswith("vc2-")
                ]
            except Exception as exc:
                _log.warning("Failed to fetch Vultr plans for sizing: %s", exc)

        # ── Phase 0: Assign team indexes (only needed when firewall is present) ──
        if has_firewall:
            teams_ordered = sorted(teams, key=lambda t: t.id)
            for idx, team in enumerate(teams_ordered, start=1):
                team.team_index = idx
            db.commit()
            teams = teams_ordered  # use sorted order from here

            # ── Phase 1: Create Vultr VPCs ────────────────────────────────────
            # Find firewall spec to get the region
            firewall_region = VULTR_DEFAULT_REGION
            for spec in vm_quota.values():
                if spec.get("role") == "firewall":
                    firewall_region = spec.get("region") or VULTR_DEFAULT_REGION
                    break

            for team in teams:
                _log.info("Creating VPC for team %d (index %d)", team.id, team.team_index)
                _create_team_vpc(team.id, event_id, firewall_region)
            db.expire_all()  # refresh team objects with vpc_id values

        # ── Create all VM records ─────────────────────────────────────────────
        library = load_all_modules()
        firewall_vm_ids = []
        other_vm_ids = []
        # Track per-team target VM count for VPC IP assignment
        team_target_counter: dict[int, int] = {}

        for team in teams:
            for vm_type_key, vm_spec in vm_quota.items():
                count = vm_spec.get("count", 1)
                role = vm_spec.get("role", "target")
                default_plan = vm_spec.get("default_plan", "vc2-1c-1gb")
                region = vm_spec.get("region") or VULTR_DEFAULT_REGION

                base_type_id = vm_spec.get("base_type")
                loaded_base_type = load_base_type(base_type_id) if base_type_id else None

                for i in range(count):
                    hostname = f"{team.name}-{vm_type_key}-{i + 1}"
                    vm = VM(
                        hostname=hostname,
                        os=loaded_base_type.os if loaded_base_type else "Ubuntu 24.04 LTS x64",
                        status="creating",
                        vm_type=vm_type_key,
                        base_type=base_type_id,
                        vultr_plan=default_plan,
                        vultr_region=region,
                        team_id=team.id,
                        event_id=event_id,
                        provision_step="queued",
                        ssh_user=vm_spec.get("ssh_user", "root"),
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )

                    if role == "firewall":
                        # Firewall VMs get the gateway IP on the team VPC
                        team_idx = team.team_index or 1
                        vm.vpc_ip = f"10.{team_idx}.1.1"
                        vm.os = "FreeBSD 14 x64"  # OPNsense base OS
                        db.add(vm)
                        db.flush()
                        db.commit()
                        firewall_vm_ids.append(vm.id)

                    elif role == "target":
                        # Select modules for this VM
                        selected = select_modules(module_quota, library, base_type_id=base_type_id)
                        db.add(vm)
                        db.flush()
                        for mod in selected:
                            db.add(VMModule(
                                vm_id=vm.id,
                                module_id=mod.id,
                                module_type=mod.type,
                                difficulty=mod.difficulty,
                                points=mod.points,
                            ))
                        if available_plans and loaded_base_type is not None:
                            sized_plan = plan_for_vm(
                                base_type=loaded_base_type,
                                modules=selected,
                                vm_quota_override_plan=vm_spec.get("default_plan"),
                                available_plans=available_plans,
                            )
                            if sized_plan != vm.vultr_plan:
                                vm.vultr_plan = sized_plan

                        # Assign VPC IP if this event has a firewall
                        if has_firewall:
                            team_idx = team.team_index or 1
                            counter = team_target_counter.get(team.id, 0)
                            vm.vpc_ip = f"10.{team_idx}.1.{10 + counter}"
                            team_target_counter[team.id] = counter + 1

                        db.commit()
                        other_vm_ids.append(vm.id)

                    else:
                        # Attacker (or any other role): no modules, no VPC
                        db.add(vm)
                        db.flush()
                        db.commit()
                        other_vm_ids.append(vm.id)

        _log.info(
            "Event %d: queued %d firewall + %d other VMs across %d teams",
            event_id, len(firewall_vm_ids), len(other_vm_ids), len(teams),
        )

        if has_firewall and firewall_vm_ids:
            # ── Phase 2: Create and bootstrap firewall VMs, then wait ────────
            _log.info("Event %d: starting firewall provisioning (%d VMs)", event_id, len(firewall_vm_ids))
            firewall_threads = [
                threading.Thread(target=_run_firewall_create, args=(vid,), daemon=True)
                for vid in firewall_vm_ids
            ]
            for t in firewall_threads:
                t.start()
            for t in firewall_threads:
                t.join()
            _log.info("Event %d: all firewall VMs provisioned, starting target/attacker VMs", event_id)

        # ── Phase 3 (or only phase without firewall): target + attacker VMs ──
        for vid in other_vm_ids:
            t = threading.Thread(target=_run_vultr_create, args=(vid,), daemon=True)
            t.start()

    except Exception as exc:
        _log.exception("Event VM provisioning failed for event %d", event_id)
    finally:
        db.close()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/vm.py
git commit -m "feat: phased provisioning with firewall-first ordering and VPC interface config"
```

---

### Task 9: VM detail UI for VPC IP and firewall credentials

**Files:**
- Modify: `frontend/templates/vm_detail.html`

- [ ] **Step 1: Add VPC IP row to Connection Info card**

In `frontend/templates/vm_detail.html`, after the `ci-basetype-row` div (around line 207), add:

```html
            <div class="detail-row" id="ci-vpc-row" style="display:none;">
                <span class="detail-label">VPC IP</span>
                <span class="detail-value" id="ci-vpc-ip">—</span>
            </div>
```

- [ ] **Step 2: Add OPNsense credentials card**

After the closing `</div>` of the "Assignment" card (around line 250), add a new card:

```html
    <!-- OPNsense Firewall Credentials (shown only for firewall VMs) -->
    <div class="card" id="fw-credentials-card" style="display:none;">
        <h3>Firewall Credentials</h3>
        <div class="detail-row">
            <span class="detail-label">Web UI</span>
            <span class="detail-value"><a id="fw-web-url" href="#" target="_blank" style="color:var(--cyan);">https://—</a></span>
        </div>
        <div class="detail-row">
            <span class="detail-label">Username</span>
            <span class="detail-value">root</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">Password</span>
            <span class="detail-value">
                <span id="fw-password" style="font-family:monospace;">—</span>
                <button class="btn btn-sm" style="margin-left:0.5rem;" onclick="copyFWPassword()">Copy</button>
            </span>
        </div>
        <div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-secondary);">
            Web UI uses a self-signed certificate — accept the browser warning.
        </div>
    </div>
```

- [ ] **Step 3: Wire up the new fields in the JavaScript data loader**

In `vm_detail.html`, find the JavaScript block that populates the detail rows (around line 418, inside `function loadVMData(data)` or similar). After the line that sets `ci-basetype`, add:

```javascript
    // VPC IP
    if (data.vpc_ip) {
        document.getElementById('ci-vpc-ip').textContent = data.vpc_ip;
        document.getElementById('ci-vpc-row').style.display = '';
    }
    // OPNsense firewall credentials
    if (data.admin_password && data.ip_address) {
        var webUrl = 'https://' + data.ip_address;
        document.getElementById('fw-web-url').href = webUrl;
        document.getElementById('fw-web-url').textContent = webUrl;
        document.getElementById('fw-password').textContent = data.admin_password;
        document.getElementById('fw-credentials-card').style.display = '';
    }
```

- [ ] **Step 4: Add copyFWPassword helper function**

In the same JavaScript section, alongside the existing `copySSH()` function, add:

```javascript
function copyFWPassword() {
    var pw = document.getElementById('fw-password').textContent;
    navigator.clipboard.writeText(pw).then(function() {
        showToast('Password copied!');
    });
}
```

- [ ] **Step 5: Expose vpc_ip and admin_password from the VM detail API endpoint**

Find the VM detail API handler in `api/routes/vm.py` (the endpoint that returns JSON for a single VM — used by the JS to populate the page). Add `vpc_ip` and `admin_password` to the response dict.

Search for the route `GET /admin/vms/{vm_id}` in `api/routes/vm.py`. In the dict it returns, add:

```python
            "vpc_ip": vm.vpc_ip,
            "admin_password": vm.admin_password,
```

- [ ] **Step 6: Commit**

```bash
git add frontend/templates/vm_detail.html api/routes/vm.py
git commit -m "feat: show VPC IP and firewall credentials on VM detail page"
```

---

## End-to-End Verification

After all tasks are complete:

- [ ] **1. Validate vm_quota with firewall role**

```bash
python -m pytest tests/test_vm_quota_validation.py -v
```
Expected: 5/5 PASS

- [ ] **2. Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **3. Integration test via docker-compose**

Start the stack and create an event with this vm_quota:
```json
{
  "firewall": {"base_type": "opnsense", "count": 1, "role": "firewall", "default_plan": "vc2-2c-4gb"},
  "target": {"base_type": "ubuntu_24_server", "count": 1, "role": "target", "default_plan": "vc2-1c-2gb"},
  "attacker": {"base_type": "ubuntu_24_server", "count": 1, "role": "attacker", "default_plan": "vc2-1c-2gb"}
}
```

Create one team, start the event. Verify:
- [ ] Provisioning dashboard shows 3 VMs: 1 firewall (creating first), 1 target, 1 attacker
- [ ] Firewall VM completes before target/attacker VMs begin creating
- [ ] Firewall VM detail page shows HTTPS web URL and admin password
- [ ] Target VM detail page shows both a public IP and a VPC IP (10.1.1.10)
- [ ] OPNsense is accessible at `https://{firewall_public_ip}` (accepts browser certificate warning)
- [ ] Can SSH to OPNsense: `ssh root@{firewall_public_ip}` using platform SSH key
- [ ] Target VM has VPC interface: `ssh root@{target_public_ip} ip addr show ens7` shows the VPC IP
- [ ] Target VM can ping OPNsense gateway: `ssh root@{target_public_ip} ping -c 3 10.1.1.1`
- [ ] Attacker VM has no VPC IP (no ens7 configuration)
- [ ] Topology graph shows firewall VM with router icon
