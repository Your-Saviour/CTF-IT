# Test plan

The default Docker test suite validates AWS configuration, ownership tags, EC2 lifecycle idempotency, VPC/subnet/route/security-group reconciliation, AMI and snapshot safety, readiness classification, GameNet phase ordering, UI contracts, migrations, guest configuration, Caldera, and Semaphore integration without AWS credentials or network access.

Run:

```bash
docker compose --profile test run --rm --build tests pytest -q
docker compose config >/dev/null
git diff --check
```

Live acceptance is restricted to an approved disposable account. Set `RUN_AWS_ACCEPTANCE=1`, `AWS_ACCEPTANCE_ACCOUNT_ID`, and a unique `AWS_ACCEPTANCE_RUN_ID`. The suite creates tagged standard-VM, GameNet, and OPNsense AMI canaries. Run `scripts/aws_acceptance_cleanup.py` afterward and require an empty matching inventory.
