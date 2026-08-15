# Known Issue: Vultr VPC-Only Private Boot

**Status:** Broken and blocked from production use

**Last verified:** 2026-08-15

**Affected workflow:** Firewall-first GameNet provisioning on Vultr

## Summary

CTF-IT cannot currently complete production GameNet provisioning with stock
Ubuntu VPC-only endpoints on the tested ordinary Vultr VPC connectivity mode.
Vultr reports that the canary instance is active, `vpc_only=true`, and attached
to the requested VPC with an assigned IPv4 address and MAC address. Inside the
guest, however, the VPC interface never becomes operational.

The private-boot certification gate correctly stops before creating workload
endpoints. The gateway, OPNsense firewall, site VPC, and retained diagnostic
canary must remain in place until an operator explicitly approves cleanup.

This is a provider/guest networking compatibility issue, not evidence that the
gateway or firewall is powered off.

## Intended provisioning path

The current implementation is designed to provision in this order:

```text
allocate
  -> public team VPN gateway
  -> OPNsense firewall and site VPC
  -> gateway-to-site WireGuard tunnel
  -> control-plane WireGuard connection
  -> stock-image private-boot canary
  -> workload endpoints
  -> modules
  -> public-ingress lockdown
  -> acceptance checks
```

CTF-IT reaches the canary gate through this management path:

```text
CTF-IT control plane
  -> public SSH to team gateway
  -> site WireGuard tunnel
  -> OPNsense LAN
  -> canary VPC address
```

The public gateway is the WireGuard entry point. OPNsense remains the intended
default gateway, DNS service, firewall, and outbound NAT device for permanent
workload endpoints.

## Confirmed production observations

The following facts were verified during the retained production smoke test:

- The team VPN gateway was active and publicly reachable.
- The OPNsense firewall was powered on, had a working public WAN address, and
  had the site VPC attached.
- Vultr returned an active VPC-only Ubuntu canary with the requested VPC ID,
  provider-assigned private IPv4 address, and attachment MAC address.
- OPNsense repeatedly sent ARP requests for the canary's provider-assigned VPC
  address, but the canary never replied. The neighbor entry stayed incomplete.
- TCP port 22 was consequently unreachable over the private path.
- The canary console stopped at `systemd-networkd-wait-online.service` while
  waiting for networking.
- Root password and platform-key SSH access could not be validated. This is
  downstream of the unusable network interface and does not by itself prove a
  separate credential defect.
- No real workload endpoint instances were created after certification failed.

The smoke test did **not** validate endpoint deterministic-address conversion,
reboot persistence, OPNsense outbound NAT for endpoints, Semaphore ProxyJump
provisioning, module installation, or final lockdown. Those stages occur after
private-boot certification and remain unproven in production.

## Suspected cause

The current site VPC uses Vultr's ordinary VPC connectivity mode. Vultr's
documented workflow for private instances without public IP addresses instead
selects **Private Instance(s) behind NAT Gateway** when the instance is
deployed.

It is therefore plausible that the ordinary connectivity mode does not supply
the boot-time network configuration expected by the tested stock Ubuntu image.
This is a hypothesis, not a confirmed root cause. It must be tested with a
separate NAT-enabled VPC and a retained stock-image canary before changing the
production architecture.

A Vultr managed NAT Gateway must not silently replace OPNsense as the permanent
endpoint egress path. If NAT-enabled connectivity is required only to make the
stock image boot, the design must prove that permanent endpoint routes cannot
bypass OPNsense.

## Operational safeguards

Until this issue is resolved:

1. Do not start a new production GameNet event on Vultr.
2. Do not use **Retry Failed** on the affected event while its diagnostic
   canary must be retained. The current certification resume and cleanup paths
   can delete a previously recorded canary.
3. Do not destroy, detach, reboot, reinstall, or resize the retained gateway,
   firewall, VPC, or canary without explicit operator approval.
4. Do not infer guest connectivity from Vultr's `active` instance state or VPC
   attachment metadata. Require guest ARP, TCP, SSH, interface, route, DNS, and
   outbound-NAT checks.
5. Keep the event closed in `provision_failed`; do not bypass the certification
   gate or fall back to public endpoint interfaces.
6. Record any future provider resource IDs in the operational incident record,
   not in this repository.

Canaries are disposable during normal automated provisioning, but an operator's
explicit instruction to retain production diagnostic resources takes
precedence over automatic cleanup. The present code does not yet model that
operator hold as persisted state, so retries are unsafe while a hold is active.

## Implemented but not production-certified

The current checkpoint includes:

- Firewall-first provisioning phases and visible
  `connecting_control_plane`/`certifying_private_boot` status.
- Persisted private-boot certification records and diagnostic phases.
- Strict `vpc_only is True` and unexpected-public-address rejection.
- Stock canary creation without endpoint cloud-init or network overrides.
- VPC attachment MAC capture and MAC-based guest-interface selection.
- Gateway-based SSH and Semaphore `ProxyJump` routing.
- Two-stage endpoint conversion from the provider address to the deterministic
  zone address.
- Resume checks intended to prevent duplicate endpoint creation.
- Automated tests covering ordering, validation, canary failure, cleanup,
  address conversion, and proxy inventory behavior.

These controls prevent unsafe continuation, but they do not make the provider
combination functional. A passing unit test is not a substitute for the blocked
production network acceptance test.

## Required next investigation

Use new, isolated resources and leave the retained production resources
untouched:

1. Create a separate Vultr VPC using NAT Gateway connectivity.
2. Create one stock Ubuntu VPC-only canary with the exact production OS ID,
   plan, region, platform SSH key, and no user data.
3. Retain the canary regardless of success or failure until an operator reviews
   the evidence.
4. Verify its console boot, attachment MAC, IPv4 address, route, cloud-init,
   key-only SSH, ARP, DNS, and outbound connectivity.
5. Determine whether an endpoint can be placed permanently behind OPNsense with
   no route—privileged or otherwise—to the managed NAT Gateway.
6. Only then update CTF-IT's VPC creation and endpoint workflow and repeat the
   complete three-to-four-VM smoke test.

If managed NAT cannot be removed or made unreachable after bootstrap, it is not
an acceptable permanent topology for adversarial training endpoints. Continue
the provider evaluation separately rather than weakening the VPC-only or
OPNsense enforcement requirements.
