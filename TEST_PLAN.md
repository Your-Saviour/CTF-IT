# Test Plan

CTF-IT uses team-scoped VMs, Ansible Semaphore, Vultr, and MITRE Caldera. The former per-user Docker-image and registry workflow has been removed.

## Automated suite

Run all automated tests through the disposable Docker test target:

```bash
docker compose --profile test build tests
docker compose --profile test run --rm tests
```

The suite must cover:

- every module and base YAML file parses;
- module/base IDs are unique and referenced files exist;
- module dependencies and conflicts reference known IDs;
- quota validation and deterministic selection behavior;
- attack-tree construction and Caldera score aggregation;
- VM goal verification and pending/achieved/defended transitions;
- Caldera operation cleanup on event stop/delete and orphaned operation detection.

CI builds and runs this same target. Production dependencies remain separate from test-only dependencies.

## Static deployment checks

These checks do not start infrastructure:

```bash
bash -n quickstart.sh
cp .env.example .env
cp deploy/.env.example deploy/.env
cp deploy/caldera/config/local.yml.example deploy/caldera/config/local.yml
docker compose --file deploy/docker-compose.yml config --quiet
```

Do not commit the generated files. They are ignored and must be mode `0600` when they contain real credentials.

## Integration boundaries

Automated tests should mock external systems at their client boundaries:

- Vultr API: instance, VPC, plan, OS, and teardown responses;
- Semaphore: project, key, repository, template, task launch, polling, and output;
- Caldera: health, agents, adversaries, operations, results, and container restart;
- SSH: command execution for service/file goal verification;
- Cloudflare: DNS creation and removal.

Provisioning tests must verify the complete state machine, including failures and retries:

1. Event start creates team VPCs when a firewall role is configured.
2. Firewall VMs are created and bootstrapped before target VMs.
3. Targets attach to the correct team VPC and receive deterministic VPC IPs.
4. Target modules are assigned and deployed through Semaphore.
5. Attacker VMs skip module deployment.
6. Failures populate `provision_error` and never report the VM as active.
7. Destroying the final VM removes the team VPC.

## Manual infrastructure smoke test

The live smoke test is intentionally a separate release gate because it creates external resources and DNS records. Follow `deploy/testing_plan.md` on a clean Linux host only after automated and static checks pass.

The smoke test must confirm:

- quickstart creates owner-readable configuration without placeholders;
- all production services become healthy;
- TLS and authentication work for every routed administration service;
- a test event provisions firewall, target, and attacker VMs;
- Ansible deployment and Caldera agent check-in complete;
- all three committed goals can transition to achieved and defended;
- teardown deletes VMs, DNS records, and VPCs;
- stopping/deleting an event cleans up associated Caldera operations;
- a second quickstart run is idempotent.
