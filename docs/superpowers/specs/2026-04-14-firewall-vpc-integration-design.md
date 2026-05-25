# OPNsense Firewall & VPC Integration Design

**Date:** 2026-04-14

## Problem

CTF-IT currently provisions Vultr VMs with public IPs only — no private networking or firewalls. This limits the realism of exercises. Blue teams should have a firewall to configure and manage as part of the CTF challenge.

## Solution

Add per-team OPNsense firewalls and Vultr VPCs to CTF events. The firewall is a challenge element — it starts with a clean slate (NAT outbound only, no port forwards) and blue teams must configure rules themselves via the OPNsense web UI.

Firewalls are opt-in: admins add a `role: "firewall"` entry to the event's `vm_quota`. Events without a firewall entry work exactly as today.

## Architecture

### Network Topology (per team)

```
Internet
    |
    +-- OPNsense VM (WAN: public IP, LAN: 10.{T}.1.1/24)
    |       |  ^ Web UI (443) + SSH (22) on WAN for blue team mgmt
    |       |
    |       +-- Team VPC (10.{T}.1.0/24)
    |               +-- target-1 (public IP + 10.{T}.1.10)
    |               +-- target-2 (public IP + 10.{T}.1.11)
    |               +-- target-N (public IP + 10.{T}.1.{10+N})
    |
    +-- attacker VM (public IP only, no VPC)
```

- **T** = team index (1-based, derived from team ordering within the event)
- Target VMs keep public IPs accessible for admin SSH testing (lockdown deferred)
- OPNsense starts clean: NAT outbound for VPC, SSH+HTTPS on WAN, allow all LAN
- Attacker VMs are not on the VPC

### vm_quota Example

```json
{
  "firewall": {
    "base_type": "opnsense",
    "count": 1,
    "role": "firewall",
    "default_plan": "vc2-2c-4gb"
  },
  "target": {
    "base_type": "ubuntu_24_server",
    "count": 3,
    "role": "target",
    "default_plan": "vc2-1c-2gb"
  },
  "attacker": {
    "base_type": "ubuntu_24_server",
    "count": 1,
    "role": "attacker",
    "default_plan": "vc2-1c-2gb"
  }
}
```

## Components

### 1. OPNsense Base Type

New base type at `bases/opnsense/opnsense.yaml`:
- OS: `FreeBSD 14 x64` (OPNsense bootstraps from FreeBSD on Vultr)
- Default plan: `vc2-2c-4gb` (headroom for Suricata IDS)
- Icon: `router` (for topology graph)
- No steps/packages — provisioning via dedicated firewall playbook

### 2. Firewall Role in vm_quota

Add `"firewall"` to `VALID_ROLES` in `builder/vm_quota_validation.py`. Firewall VMs:
- Do not get modules assigned
- Do not go through the normal base playbook + module playbook flow
- Have their own provisioning chain (create VM → bootstrap OPNsense)

### 3. VPC Networking

Each team gets a dedicated Vultr VPC when the event has a firewall role. The VPC
is created via the Vultr REST API directly from `_create_team_vpc()` (a single
API call — no dedicated `create-vpc.yml` playbook is used):
- Subnet: `10.{team_index}.1.0/24`
- OPNsense LAN gateway: `10.{T}.1.1`
- Target VM IPs: `10.{T}.1.10`, `10.{T}.1.11`, etc.
- MTU 1450 (required for Vultr's VXLAN overlay)

VPC info stored on the Team model (`vpc_id`, `team_index`). Per-VM VPC IP stored on VM model (`vpc_ip`). The subnet is derived from `team_index` at runtime (`10.{team_index}.1.0/24`), so no separate `vpc_subnet` column is stored.

### 4. Provisioning Flow

When an event with a `firewall` role in vm_quota is started:

1. **Assign team indexes** (1-based) for subnet calculation
2. **Create VPCs** — one per team via `vultr.cloud.vpc` module
3. **Create firewall VMs** — with VPC attachment, then bootstrap OPNsense. Wait for all firewalls to reach `active` before proceeding.
4. **Create target VMs** — with VPC attachment via optional `vpc_description` param in `create-vm.yml`. After normal provisioning, configure VPC interface (netplan + static IP).
5. **Create attacker VMs** — no VPC, same as today

Sequencing ensures the OPNsense gateway is up before target VMs try to route through it.

### 5. New Playbooks

| Playbook | Purpose |
|----------|---------|
| `create-firewall.yml` | Create OPNsense VM with VPC attachment |
| `bootstrap-opnsense.yml` | Convert FreeBSD to OPNsense (adapted from cloudlab) |
| `configure-vpc-interface.yml` | Set up VPC network interface on target VMs |

### 6. Templates

| Template | Purpose |
|----------|---------|
| `templates/opnsense_config.xml.j2` | OPNsense config.xml (adapted from cloudlab) |
| `templates/vpc-netplan.yaml.j2` | Netplan config for target VM VPC interface |

### 7. OPNsense Config (Clean Slate)

The config.xml template configures:
- WAN: `vtnet0` (DHCP, public IP)
- LAN: `vtnet1` (`10.{T}.1.1/24`)
- Firewall: allow HTTPS+SSH inbound on WAN (for management), allow all outbound, allow all LAN
- NAT: automatic outbound mode, **no port forwards**
- SSH: enabled with root login
- Web UI: HTTPS on 443, self-signed cert

No port forwarding rules are pre-configured. Blue teams must create these themselves.

### 8. Credential Storage

OPNsense admin password (randomly generated during bootstrap) stored on the VM record in a new `admin_password` field. Displayed on the VM detail page for firewall VMs.

## Database Changes

**Team model — new columns:**
- `vpc_id` (String 64, nullable) — Vultr VPC UUID
- `vpc_subnet` (String 18, nullable) — e.g. "10.1.1.0"
- `team_index` (Integer, nullable) — 1-based, for subnet calculation

**VM model — new columns:**
- `vpc_ip` (String 45, nullable) — private IP on team VPC
- `admin_password` (String 128, nullable) — OPNsense admin password (firewall VMs only)

All nullable for SQLite compatibility.

## UI Changes

- **VM detail page:** Show VPC IP for target VMs, show OPNsense credentials + web URL for firewall VMs
- **Topology graph:** Firewall VMs render with router icon (via base type `icon: router`)
- **Event form / provisioning dashboard:** No changes — firewall VMs appear in existing tables

## Modifications to Existing Files

| File | Change |
|------|--------|
| `builder/vm_quota_validation.py` | Add `"firewall"` to `VALID_ROLES` |
| `api/models.py` | Add VPC/firewall fields to Team and VM |
| `api/routes/vm.py` | Phased provisioning, VPC creation, firewall bootstrap |
| `playbooks/create-vm.yml` | Optional `vpc_description` param for VPC attachment |
| VM detail template | Display firewall creds, VPC IP |

## Out of Scope

- OPNsense-specific vulnerability/hardening modules
- iptables lockdown on target VMs (public IPs stay open for testing)
- VPN access for blue teams (future work)
- Auto-generated port-forward rules from module metadata
- Event-level bulk VPC cleanup (per-VM destroy now tears down the VPC once the
  team's last VPC-attached VM is removed, via `_maybe_cleanup_team_vpc`)
