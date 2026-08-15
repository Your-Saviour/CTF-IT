# AWS acceptance

These tests are inert unless `RUN_AWS_ACCEPTANCE=1`. Also provide the approved `AWS_ACCEPTANCE_ACCOUNT_ID`, a unique `AWS_ACCEPTANCE_RUN_ID` of at least eight characters, `AWS_ENVIRONMENT=acceptance`, `CTF_CONTROL_PLANE_CIDR`, and all normal AWS configuration. The fixture verifies STS identity and an initially empty run inventory before any canary may mutate AWS.

The suite builds and retires an OPNsense AMI, provisions a complete GameNet through the production state machine, checks private endpoints and firewall egress, and creates/configures/destroys an ordinary VM. Every canary resource includes `ManagedBy=ctf-it`, `Environment=acceptance`, and `AcceptanceRunId=<run id>`.

GameNet configures a local WireGuard interface, so run pytest on the dev host as root or in a container with `NET_ADMIN`, `/dev/net/tun`, and routing access to the host network. Do not run the canary in the normal unprivileged test container.

```bash
sudo --preserve-env=AWS_DEFAULT_REGION,AWS_ENVIRONMENT,AWS_STANDARD_VPC_ID,AWS_STANDARD_SUBNET_ID,AWS_STANDARD_SECURITY_GROUP_IDS,AWS_UBUNTU_AMIS,AWS_FREEBSD_AMIS,AWS_AVAILABILITY_ZONES,AWS_INSTANCE_TYPES,AWS_KEY_PAIR_NAME,CTF_CONTROL_PLANE_CIDR,RUN_AWS_ACCEPTANCE,AWS_ACCEPTANCE_ACCOUNT_ID,AWS_ACCEPTANCE_RUN_ID \
  python -m pytest -q tests/aws_acceptance
python scripts/aws_acceptance_cleanup.py --run-id run-unique-001 \
  --expected-account-id 123456789012 --inventory-only
```

The session fixture always attempts tagged cleanup. If pytest is interrupted, run the cleanup script without `--inventory-only`; it removes only exact matching run-tag resources and then exits nonzero unless the final inventory is empty.
