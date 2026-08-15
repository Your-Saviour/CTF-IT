# CTF-IT

CTF-IT is a VM-based red-team/blue-team training platform. Administrators create events and teams, provision EC2 instances, apply vulnerability and hardening modules through Ansible Semaphore, and run adversary operations through MITRE Caldera.

## AWS configuration

Production uses the IAM role attached to the API workload. Local development may set `AWS_PROFILE` in the invoking shell. The application never accepts or stores AWS access keys.

Required configuration:

```dotenv
AWS_DEFAULT_REGION=ap-southeast-2
AWS_ENVIRONMENT=production
AWS_STANDARD_VPC_ID=vpc-replace-me
AWS_STANDARD_SUBNET_ID=subnet-replace-me
AWS_STANDARD_SECURITY_GROUP_IDS=sg-replace-me
AWS_UBUNTU_AMIS={"ap-southeast-2":"ami-replace-me"}
AWS_FREEBSD_AMIS={"ap-southeast-2":"ami-replace-me"}
AWS_AVAILABILITY_ZONES={"ap-southeast-2":"ap-southeast-2a"}
AWS_INSTANCE_TYPES=t3.small,t3.medium,t3.large
CTF_CONTROL_PLANE_CIDR=203.0.113.10/32
```

Attach [deploy/aws/iam-policy.json](deploy/aws/iam-policy.json) to the production workload role. Confirm the approved AMIs, instance-type offerings, standard subnet capacity, Elastic IP quota, VPC quota, ENI demand, and On-Demand vCPU quota before starting an event. The start endpoint performs these checks and keeps the event closed if any blocking check fails. Pricing lookup failures are reported but do not hide successful capacity checks.

## VM lifecycle

The admin UI creates ordinary Ubuntu VMs in the configured standard subnet. EC2 instances, ENIs, EBS volumes, Elastic IPs, key pairs, and GameNet resources carry `Application=ctf-it`, `ManagedBy=ctf-it`, `Environment`, and applicable event/team/site/VM tags. Creation uses stable EC2 client tokens; retries reconcile persisted IDs. Destruction verifies ownership tags before terminating or releasing resources.

Semaphore configures guests after EC2 is reachable. It does not create or delete AWS resources.

## GameNet architecture

Each event team receives a public VPN gateway in the standard VPC. Every site receives a single-AZ VPC with:

- a small public WAN subnet and Elastic IP for the OPNsense firewall;
- an infrastructure LAN subnet;
- one isolated `/24` subnet per event zone;
- exact, owned WAN/LAN/zone security groups;
- private route tables whose default route targets the firewall LAN ENI.

The firewall has separate WAN and LAN ENIs with source/destination checking disabled. Endpoints have private ENIs only. WireGuard retains the existing team, site-isolation, control-plane, and participant profile behavior. The event is opened only after security lockdown and connectivity/exposure acceptance checks pass.

## OPNsense AMIs

Admin → Settings → OPNsense Images starts a resumable build from the approved regional FreeBSD AMI. The workflow creates a tagged temporary VPC, bootstraps and validates OPNsense, performs a clean halt, creates an AMI, records every backing EBS snapshot, and launches independent validation clones. Activation requires public and private-network evidence plus unique SSH host keys. Retirement refuses referenced images and verifies ownership before deregistering the AMI and deleting snapshots.

## Development and tests

```bash
cp .env.example .env
docker compose --profile test run --rm --build tests pytest -q
docker compose up --build
```

Normal tests require no AWS credentials and make no AWS network calls. Disposable live canaries require all of:

```bash
RUN_AWS_ACCEPTANCE=1
AWS_ENVIRONMENT=acceptance
AWS_ACCEPTANCE_ACCOUNT_ID=123456789012
AWS_ACCEPTANCE_RUN_ID=unique-run-id
CTF_CONTROL_PLANE_CIDR=203.0.113.10/32
```

Run them only in the approved test account. Every canary resource is tagged with the run ID, and the cleanup inventory must be empty afterward. See [tests/aws_acceptance/README.md](tests/aws_acceptance/README.md).

## Legacy records

Database columns for pre-cutover cloud records remain readable for audit/history, but the application exposes no operation that creates, changes, or deletes those historical resources. The former private-network issue note is archived under `docs/historical/`.
