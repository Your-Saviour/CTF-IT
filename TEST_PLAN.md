# Test plan

The default Docker test suite validates AWS configuration, ownership tags, EC2 lifecycle idempotency, VPC/subnet/route/security-group reconciliation, AMI and snapshot safety, readiness classification, GameNet phase ordering, UI contracts, migrations, guest configuration, Caldera, and Semaphore integration without AWS credentials or network access.

Run:

```bash
docker compose --profile test run --rm --build tests pytest -q
docker compose config >/dev/null
git diff --check
```

Live acceptance is restricted to an approved disposable account. Authenticate with `docker compose --profile aws-acceptance run --rm aws-tools login --remote --profile ctf-it-acceptance --region ap-southeast-2`. Set `RUN_AWS_ACCEPTANCE=1`, `AWS_ENVIRONMENT=acceptance`, `AWS_ACCEPTANCE_ACCOUNT_ID`, a unique `AWS_ACCEPTANCE_RUN_ID`, and `CTF_CONTROL_PLANE_CIDR`, then run `docker compose --profile aws-acceptance run --rm --build aws-acceptance`. The suite creates tagged standard-VM, full GameNet, and OPNsense AMI canaries. Compose supplies WireGuard, host networking, `NET_ADMIN`, and `/dev/net/tun`; no host Python or AWS CLI is required. The session fixture performs cleanup; run `python scripts/aws_acceptance_cleanup.py --inventory-only` through the same acceptance service afterward and require an empty matching inventory. Without `--inventory-only`, that containerized script is the interrupt-recovery cleanup path.
The explicit `/tmp` database keeps test startup independent of whether a local
`data/` directory exists and prevents the suite from touching development data.

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
openssl genpkey -algorithm RSA -aes-256-cbc -pass pass:test-only \
  -pkeyopt rsa_keygen_bits:3072 -out deploy/caldera/config/ssh_host_key
docker compose --file deploy/docker-compose.yml config --quiet
```

Do not commit the generated files. They are ignored and must be mode `0600` when they contain real credentials. Replace the Caldera host-key path and passphrase placeholders before starting services.

## Integration boundaries

Automated tests should mock external systems at their client boundaries:

- Vultr API: instance, VPC, plan, OS, and teardown responses;
- Semaphore: project, key, repository, template, task launch, polling, and output;
- Caldera: health, agents, adversaries, operations, results, and container restart;
- SSH: command execution for service/file goal verification;
- Cloudflare: DNS creation and removal.

The managed-image tests additionally cover the 26.7/FreeBSD 15 mapping,
pre-mutation CIDR and Vultr availability gates, official bootstrap URL and hash
recording, WAN-only/key-only golden configuration, interrupted conversion
resume, two builder boots, snapshot blocking, both disposable clone paths,
VPC MAC-based LAN configuration, unique host keys, and cleanup failures after
readiness. No new image test may import or attach installation media, add a
builder VPC, or expose a manual installation-completion action.

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
- the CTF API starts against its dedicated PostgreSQL database;
- API, Semaphore, Caldera, and AI-agent communicate through Docker service names;
- all production services become healthy;
- TLS and authentication work for every routed administration service;
- a test event provisions firewall, target, and attacker VMs;
- Ansible deployment and Caldera agent check-in complete;
- all three committed goals can transition to achieved and defended;
- teardown deletes VMs, DNS records, and VPCs;
- stopping/deleting an event cleans up associated Caldera operations;
- a second quickstart run is idempotent.

## Expo-IT green-service live acceptance

The default suite exercises the deployment, secret, retry, integration, and firewall boundaries with fakes. A release using green infrastructure must additionally use a disposable Vultr VM and a read-only repository deploy key:

```bash
docker compose --profile test run --rm \
  -e EXPO_IT_GREEN_LIVE=1 \
  -e EXPO_IT_GIT_SSH_KEY_PATH=/run/secrets/expo_it_deploy_key \
  -e EXPO_IT_GREEN_TARGET=root@203.0.113.10 \
  tests pytest tests/test_expo_it_green_live.py -m expo_it_green_live -q
```

The key path must be mounted into the test container and the target must be a disposable Ubuntu 24.04 VM reachable with the test runner's normal SSH identity. Never use a production Expo-IT host. After the automated check, verify:

- the resolved checkout equals `origin/stable` and authenticated `/api/v1/data` validates;
- HTTPS succeeds through every event team's GameNet VPN;
- HTTPS/SSH fail from an unrelated public source after lockdown;
- the green VM cannot initiate traffic to any allocated team site CIDR;
- the event shows one shared green VM and one enabled owned Expo-IT binding;
- the Git SSH-key fact is absent after success but remains encrypted after an induced failure;
- Retry reuses the VM and owned integration records; and
- event deletion removes the disposable VM and owned records while preserving administrator-managed destinations.
