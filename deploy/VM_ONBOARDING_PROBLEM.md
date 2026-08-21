# VM onboarding and AWS provisioning

Existing machines can be registered with hostname, address, SSH user, and team. Managed machines are created through the AWS cloud endpoint and configured by Semaphore after SSH becomes reachable.

AWS is the only active provider for newly managed infrastructure. Pre-cutover Vultr identifiers may still appear on historical database rows, but operators must manage any surviving Vultr resources outside CTF-IT; the application will not mutate or delete them.

GameNet event start requires an active validated OPNsense AMI and a passing AWS readiness report. Failures leave the event closed and retain persisted AWS IDs for reconciliation or owned cleanup.

Production relies on the API workload IAM role, AWS service availability, quota headroom, Semaphore, and optionally Cloudflare. Attach [the least-privilege policy](aws/iam-policy.json) to the workload role and set the full AWS configuration block in `deploy/.env`; the root `.env` continues to hold API secrets and application defaults. Do not store AWS access keys in either file.

Before a scheduled event, verify:

- STS resolves the intended AWS account and workload role;
- the approved Ubuntu and FreeBSD AMI maps cover every selectable region;
- the standard VPC, subnet, and security groups exist in `AWS_DEFAULT_REGION`;
- the configured Availability Zone offers every allowed instance type;
- subnet addresses, Elastic IPs, VPCs, ENIs, and On-Demand vCPU quotas cover the event plan; and
- `CTF_CONTROL_PLANE_CIDR` is the narrow IPv4 CIDR allowed to perform temporary builder and management access.

The event readiness gate repeats these checks before mutation and keeps the event closed if capacity, identity, permissions, images, or networking cannot be proven. Pricing lookup failures make cost unavailable but do not bypass the capacity checks.
