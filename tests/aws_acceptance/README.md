# AWS acceptance

These tests are inert unless `RUN_AWS_ACCEPTANCE=1`. Also provide the approved `AWS_ACCEPTANCE_ACCOUNT_ID`, a unique `AWS_ACCEPTANCE_RUN_ID` of at least eight characters, and all normal AWS configuration. The fixture verifies STS identity before any canary may mutate AWS.

Every canary resource must include `ManagedBy=ctf-it`, `Environment=acceptance`, and `AcceptanceRunId=<run id>`. Run the cleanup inventory afterward and treat any remaining matching resource as a failed acceptance run.

```bash
RUN_AWS_ACCEPTANCE=1 AWS_ACCEPTANCE_ACCOUNT_ID=123456789012 AWS_ACCEPTANCE_RUN_ID=run-unique-001 \
  docker compose --profile test run --rm tests pytest -q tests/aws_acceptance
python scripts/aws_acceptance_cleanup.py --run-id run-unique-001 --expected-account-id 123456789012
```
