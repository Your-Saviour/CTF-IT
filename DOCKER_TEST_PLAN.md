# Docker test plan

Copy `.env.example` to `.env`, replace local non-secret AWS resource identifiers, and run `docker compose config`. Production credentials come from the workload IAM role; local credentials may come from `AWS_PROFILE`. Do not put access keys in Compose or env templates.

Verify API health, database persistence, Semaphore guest configuration, and the offline pytest suite. AWS live canaries remain separately opt-in.
