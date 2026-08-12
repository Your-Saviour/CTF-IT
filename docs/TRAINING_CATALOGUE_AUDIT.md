# Training catalogue release audit

This release audits the original 62 definitions and the Linux-first expansion
through the same loader and contract used by event readiness. The current
catalogue contains 75 modules: 46 vulnerabilities, 8 hardening tasks, 8 payload
investigations, 8 external application foundations, 2 internal foundations,
and 3 cyclical goals.

## Applied review standard

- Easy tasks stay within one system area and receive guided diagnosis.
- Medium tasks require investigation and a multi-step remediation.
- Hard tasks require chained evidence, source or configuration changes, and a
  service-health regression check.
- Every visible task has objectives, an estimate, progressive hints, a
  root-cause debrief, remediation principles, and an ATT&CK mapping.
- All 65 learner exercises have at least two titled authoritative HTTPS references: a primary technical source and a security-context source.
- CVE tasks include both the relevant vendor advisory and an NVD or CISA entry.
- Application-dependent checks are automatically composed with the
  foundation's health check so breaking the service cannot earn points.
- Definitions, paths, identifiers, ports, dependency edges, conflicts, stages,
  and verification types are validated before readiness can pass.

## Material corrections

- Log4Shell now inspects both bundled Log4j libraries for version 2.17.1 or
  later and requires the service health endpoint to remain healthy.
  `formatMsgNoLookups` is retained only as historical mitigation context.
- Stored XSS now makes a fresh POST, rejects raw script output, requires encoded
  output, and proves that legitimate guestbook posting still works.
- The monitord SUID shell script is explicitly an audit/compliance finding;
  it no longer claims Linux executes interpreted SUID scripts with elevation.
- Rogue SSH key, cron beacon, shell-profile hook, and uploaded webshell content
  are payload investigations. Rogue systemd and tampered SSH configuration
  complete the six required core investigations.
- SSH hardening checks use effective `sshd -T` state and service health. IP
  forwarding checks runtime sysctl state. Firewall work requires an active
  default-deny policy. Privileged-container work inspects Docker runtime state.
- Caldera placeholder callback domains were removed from active definitions;
  remaining abilities must return observable command output rather than an
  unconditional success message.

## Expansion and presets

The expansion adds audit coverage, journal retention, authentication-log
investigation, persistence triage, firewall least privilege, Docker socket
exposure, privileged runtime, environment-secret leakage, immutable image
selection, a controlled Docker foundation, and a backup agent. Four curated
presets are loadable through `quota.preset`; selector dependency and conflict
rules still apply. Contract validation requires every preset to include an
investigation and a multi-step remediation.

Windows, Active Directory, and a dedicated enterprise SIEM are intentionally
outside this Linux/Ubuntu/Vultr release.
