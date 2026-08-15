# Cyber Training Planner Icon Pack Design

## Goal

Replace the planner's small, inconsistent icon set with a coherent library that covers common cyber-range infrastructure, roles, services, platforms, and cloud workloads without breaking saved plans.

## Visual Direction

The planner retains its existing industrial topology language: monochrome filled SVG silhouettes, a 24×24 view box, cyan/amber/red state colouring, and a large primary icon overlapped by a smaller secondary product icon. Generic icons communicate function; simplified recognizable product marks communicate platform. Icons must remain legible at both 36px primary and 24px secondary sizes.

## Library and Categories

The library will contain approximately 50 icons grouped as:

- Devices: server, desktop, laptop, mobile, appliance.
- Network: gateway, router, switch, firewall, VPN, proxy, load balancer.
- Services: web, database, DNS, mail, directory, file share, storage, certificate authority, identity provider.
- Security: attacker, target, SIEM, IDS/IPS, monitoring, logging, honeypot, malware, bastion, vulnerable host.
- Workloads: cloud, container, Kubernetes, backup, Git, CI/CD.
- Platforms: Linux, Ubuntu, Debian, Kali, Red Hat, Windows, macOS, FreeBSD, OPNsense, pfSense.
- Cloud providers: AWS, Azure, Google Cloud.

Every existing keyword remains supported. Duplicate placeholder artwork for Linux distributions is replaced with distinguishable simplified marks. New entries use stable lowercase keywords and human-readable labels.

## Data and UI

Each built-in icon entry gains a `category` alongside `label` and `path`. `PLANNER_ICON_GROUPS` projects the ordered categories for rendering. Both Primary icon and Secondary icon selectors display the complete library in category-labelled `<optgroup>` sections while retaining their existing Automatic choice. Saved `primary_icon` and `icon` fields and resolution priority do not change.

## Validation and Compatibility

Client and server validation derive from matching explicit keyword sets. Existing values remain valid. Unknown values remain rejected. Custom trusted base-type SVG metadata continues to resolve as before. Provisioning ignores icon fields.

## Testing

Executable JavaScript tests verify category coverage, unique keywords, valid SVG paths, preserved legacy keys, pair resolution, and grouped options. Python tests verify server allowlist parity and acceptance/rejection. Syntax checks, the full Docker test suite, independent review, and a live port 8091 rebuild complete the change.
