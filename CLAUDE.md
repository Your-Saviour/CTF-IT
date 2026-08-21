# Repository guide

CTF-IT provisions AWS EC2 infrastructure and configures guests with Ansible Semaphore. AWS mutations belong in `api/services/aws/`; HTTP routes stay thin. Use Boto3's standard credential chain and never add access-key inputs or stored cloud secrets.

Important areas:

- `api/services/aws/config.py`: required region, VPC/subnet, approved AMIs, AZs, and instance types.
- `api/services/aws/compute.py`: owned EC2/key/EIP lifecycle.
- `api/services/aws/network.py`: VPC, subnet, route, ENI, and exact security-group reconciliation.
- `api/services/aws/images.py` and `opnsense_workflow.py`: AMI and EBS snapshot lifecycle.
- `api/services/aws/readiness.py`: identity, offering, address, and quota gates.
- `api/services/cloud_provisioning.py`: ordinary VM orchestration.
- `api/services/gamenet_provisioning.py`: durable event GameNet phases.
- `builder/plan_sizing.py`: EC2 resource sizing.

All supported resources must carry canonical ownership tags. Destructive operations must read and validate those tags first. Preserve historical database columns as read-only compatibility data; do not reintroduce runtime operations for the retired provider.

Run the offline suite with `docker compose --profile test run --rm --build tests pytest -q`. Live AWS acceptance is opt-in and requires an approved account ID and unique run ID.

Current-facing documentation must describe AWS as the sole provisioning provider. Vultr references are acceptable only in migrations, compatibility notes, and clearly historical specs/plans. Azure and GCP remain valid planner icon keywords, not supported provisioning backends.
