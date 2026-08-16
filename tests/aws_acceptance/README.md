# AWS acceptance

These tests are inert unless `RUN_AWS_ACCEPTANCE=1`. Also provide the approved `AWS_ACCEPTANCE_ACCOUNT_ID`, a unique `AWS_ACCEPTANCE_RUN_ID` of at least eight characters, `AWS_ENVIRONMENT=acceptance`, `CTF_CONTROL_PLANE_CIDR`, and all normal AWS configuration. The fixture verifies STS identity and an initially empty run inventory before any canary may mutate AWS.

The suite builds and retires an OPNsense AMI, provisions a complete GameNet through the production state machine, checks private endpoints and firewall egress, and creates/configures/destroys an ordinary VM. Every canary resource includes `ManagedBy=ctf-it`, `Environment=acceptance`, and `AcceptanceRunId=<run id>`.

GameNet configures a WireGuard interface inside the dedicated acceptance container. The Compose service supplies `NET_ADMIN`, `/dev/net/tun`, and host networking; do not run the canary on the host or in the normal unprivileged test container.

```bash
docker compose --profile aws-acceptance run --rm aws-tools \
  login --remote --profile ctf-it-acceptance --region ap-southeast-2

AWS_PROFILE=ctf-it-acceptance \
RUN_AWS_ACCEPTANCE=1 \
AWS_ENVIRONMENT=acceptance \
AWS_ACCEPTANCE_ACCOUNT_ID=123456789012 \
AWS_ACCEPTANCE_RUN_ID=run-unique-001 \
docker compose --profile aws-acceptance run --rm --build aws-acceptance

AWS_PROFILE=ctf-it-acceptance \
AWS_DEFAULT_REGION=ap-southeast-2 \
docker compose --profile aws-acceptance run --rm aws-acceptance \
  python scripts/aws_acceptance_cleanup.py --run-id run-unique-001 \
  --expected-account-id 123456789012 --inventory-only
```

The named `aws_credentials` volume stores only temporary AWS login state and is shared by the two tooling containers. The session fixture always attempts tagged cleanup. If pytest is interrupted, run the containerized cleanup command without `--inventory-only`; it removes only exact matching run-tag resources and then exits nonzero unless the final inventory is empty.
