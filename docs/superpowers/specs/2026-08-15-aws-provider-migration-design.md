# AWS Provider Migration Design

**Date:** 2026-08-15

**Status:** Approved in conversation

## Objective

Replace every active Vultr integration with AWS while preserving the platform's standard VM lifecycle and its GameNet learner experience. GameNet retains its OPNsense firewalls, WireGuard gateways, isolated sites and zones, module deployment, and acceptance checks. The migration applies only to newly provisioned infrastructure; it does not migrate or destroy existing Vultr resources.

## Scope

The cutover covers standard VM creation and destruction, event-driven provisioning, GameNet VPC networking, OPNsense image building and certification, capacity and readiness checks, instance sizing and pricing, credentials, database fields, routes and payloads, UI copy, tests, deployment configuration, and documentation.

Existing Vultr rows remain readable for historical database compatibility, but no post-cutover runtime path manages them. Cloudflare DNS and Semaphore remain supported. Semaphore configures guests and deploys modules; it no longer creates or destroys cloud resources.

## Chosen Approach

Introduce a first-class AWS provider layer backed by Boto3. This is preferred to scattering AWS calls through existing routes and to introducing CloudFormation or Terraform stacks whose state would compete with the application's database and provisioning state machines.

The application database remains the authoritative workflow record. AWS is reconciled through stable tags, persisted resource identifiers, deterministic client tokens where available, and find-before-create behavior elsewhere.

## Configuration and Authentication

Boto3 uses the standard AWS credential chain. Production should use an IAM role attached to the application runtime; local development may use `AWS_PROFILE` or standard AWS access-key environment variables. The application must not accept or persist AWS secret keys through its service-credentials database entry.

Required and optional configuration will include:

- `AWS_DEFAULT_REGION` for ordinary VM provisioning and default GameNet placement.
- Optional `AWS_PROFILE` for local operation.
- An administrator-managed VPC ID and subnet ID for ordinary VMs.
- `CTF_CONTROL_PLANE_CIDR` for temporary builder and management access.
- Approved Ubuntu and FreeBSD AMI IDs per supported region. AMI selection is explicit and allow-listed; it must not silently select an arbitrary public image.
- An allow-listed instance-type catalogue used by resource sizing.
- The existing optional Cloudflare configuration.

The repository will document a least-privilege IAM policy covering only the EC2, VPC, image, tagging, pricing or catalogue lookup, identity, and quota-read operations the application uses. Destructive calls are limited in code to resources bearing the application's ownership tags.

## Provider Boundaries

The provider package contains small services with injected Boto3 clients:

- `AwsSessionFactory` creates regional clients through the standard credential chain and exposes caller-identity validation.
- `AwsComputeProvider` manages key pairs, instances, Elastic IPs, instance waiters, source/destination checking, tags, and termination.
- `AwsNetworkProvider` manages VPCs, subnets, internet gateways, route tables, ENIs, security groups, and dependency-ordered cleanup.
- `AwsImageProvider` manages builder instances, AMI creation, backing EBS snapshots, validation launches, activation metadata, retirement, and cleanup.
- A provider-neutral sizing service maps module `min_ram_mb` and `min_vcpu` requirements to an approved EC2 instance type offered in the selected Availability Zone.

Routes and orchestration code consume these interfaces and do not construct raw AWS clients. Provider exceptions distinguish retryable AWS conditions from permission, quota, invalid-configuration, and terminal resource errors.

## Resource Ownership

Every managed AWS resource receives these tags where supported:

- `Application=ctf-it`
- `ManagedBy=ctf-it`
- `Environment=<configured environment>`
- `EventId=<database event id>` when applicable
- `TeamId=<database team id>` when applicable
- `SiteId=<database site id>` when applicable
- `VmId=<database VM id>` when applicable

Create operations reconcile by resource ID, client token, and ownership tags before creating replacements. Cleanup verifies ownership tags and expected database identity. A tag mismatch is a hard stop, not an invitation to delete the resource.

## Standard VM Architecture

Ordinary VMs launch into an administrator-configured VPC and subnet. The provider reconciles the platform key material and security group, launches the approved Ubuntu AMI with the selected instance type, waits for EC2 and SSH readiness, then persists instance, IP, network-interface, region, and Availability Zone data. Cloudflare DNS remains optional. Semaphore subsequently performs base setup, module deployment, sealing, and Caldera-agent deployment as it does today.

Manual creation endpoints return provider-neutral fields such as `cloud_instance_id`, `instance_type`, `region`, and `availability_zone`. Current Vultr-specific routes and payload names are removed after their callers switch.

## GameNet Network Architecture

Each GameNet site has an AWS VPC in its configured region and a selected Availability Zone. The VPC contains:

- A public WAN subnet with an internet gateway.
- An isolated infrastructure/LAN subnet.
- One private subnet for each GameNet zone.
- Explicit route tables sending zone and infrastructure egress through the OPNsense LAN ENI.

The OPNsense EC2 instance has a WAN ENI and a LAN ENI in the same Availability Zone, because AWS only permits ENI attachment within one Availability Zone. Its WAN side receives an Elastic IP, source/destination checking is disabled, and security groups implement only the bootstrap and final exposure allowed by the existing design. Private target and attacker endpoints have no public IP.

The public team WireGuard gateway remains the learner access hub. It maintains the current per-team participant profiles, site tunnels, cross-site discovery, and site-to-site isolation policy. OPNsense remains the site's internet edge and routing/security boundary; AWS route tables and security groups enable that topology but do not replace its policy role.

Region remains a site-level placement choice. Availability Zone becomes stored site metadata because all subnets and multi-NIC appliances in a site must agree on it. Cross-region sites continue to communicate through the existing encrypted WireGuard overlay rather than VPC peering.

## OPNsense Image Workflow

For every supported region, an administrator supplies an approved FreeBSD base AMI. The workflow:

1. Preflights the AMI, instance type, subnet, IAM permissions, and control-plane CIDR.
2. Creates a tagged temporary builder VPC with public and isolated validation subnets, a security group, and a FreeBSD instance.
3. Bootstraps and validates the requested OPNsense release.
4. Sanitizes the guest and performs a clean halt.
5. Creates an EBS-backed AMI and waits until it is available.
6. Launches two tagged validation instances to exercise public-management boot and isolated/private-path boot, including NIC mapping and configuration fingerprint checks.
7. Marks the AMI active only after both validations pass.
8. Retires an old AMI by deregistering it and deleting only its tracked backing snapshots after no VM record references it.

Failed candidates never become active. The database records AMI ID, backing snapshot IDs, builder and validation resource IDs, region, Availability Zone, phases, validation evidence, and cleanup state. The old Vultr ISO fields remain nullable historical columns only.

## Provisioning Data Flow

Event preflight resolves AMIs and instance types, validates AWS identity and permissions, checks regional and Availability Zone offerings, and calculates required VPCs, subnets, ENIs, Elastic IPs, security groups, instances, vCPU, and address capacity before changing the event.

Standard VM provisioning follows network lookup, key and security-group reconciliation, EC2 launch, readiness wait, address persistence, optional DNS creation, and Semaphore configuration.

GameNet provisioning follows site networking, WireGuard gateway creation, OPNsense ENIs and instance, routing, private endpoints, appliance and tunnel configuration, module deployment, connectivity and exposure checks, and removal of temporary management access. An event cannot become open until all required acceptance checks pass.

## State, Retries, and Cleanup

Each successful AWS mutation persists its identifier before the workflow advances. Phases are durable and retry resumes by reconciling recorded resources rather than blindly recreating them. Retryable throttling, eventual-consistency, and transient-state errors receive bounded exponential backoff with jitter. Permission, quota, ownership, invalid AMI, invalid network, and invalid configuration errors stop immediately with actionable messages.

Destroy operations first mark records `destroying`, remove DNS, then delete instances, release Elastic IPs, delete ENIs and routes, remove security groups and subnets, detach and delete internet gateways, and finally delete VPCs. Database records are removed only after provider cleanup succeeds. Incomplete cleanup preserves identifiers and exposes retry status. Any force-forget operation is separate, explicit, admin-only, and never deletes unverified AWS resources.

## Schema and Compatibility

New migrations introduce provider-neutral identifiers and AWS metadata, including cloud instance ID, instance type, region, Availability Zone, ENI IDs, VPC/subnet/route/security-group identifiers, AMI ID, and backing snapshot IDs. Relevant generic values may be copied from legacy fields only when their meaning is identical; Vultr resource identifiers are never reinterpreted as AWS identifiers.

Application models, schemas, APIs, templates, and JavaScript switch to provider-neutral or AWS-specific terminology. Historical Vultr columns remain readable so existing databases upgrade without destructive column removal. There is no compatibility promise for creating, retrying, or destroying legacy Vultr resources after cutover.

## Readiness and Capacity

Readiness verifies caller identity, supported region and Availability Zone, approved AMIs, instance-type availability, configured ordinary-VM network, subnet address capacity, Elastic IP capacity, VPC and ENI quotas, On-Demand vCPU quotas, and required IAM operations. Event plan preview reports projected AWS resources and cost inputs and blocks start when capacity cannot be proven.

The deployment guide calls out low default VPC and Elastic IP quotas and requires operators to request increases before scheduling an event that exceeds them. Price lookup failure may mark cost as unavailable but must not bypass capacity or identity validation.

## Testing

Provider unit tests inject Botocore Stubber responses or narrow fake clients and cover create reconciliation, waiter transitions, address extraction, ownership enforcement, rollback, cleanup ordering, throttling, and terminal errors. Orchestration tests use fake provider interfaces and verify phase persistence, retry, partial failure, and event-open gating. Existing GameNet tests are translated from Vultr fixtures to AWS resources and semantics.

An opt-in disposable AWS acceptance suite, excluded from the normal Docker suite, creates only uniquely tagged resources in an approved test account. It covers standard create/configure/destroy, GameNet VPC/subnets, an OPNsense dual-ENI instance, a private endpoint, WireGuard reachability, exposure checks, and complete cleanup. A final tag inventory must find no resources from the run.

The normal Docker test suite remains the required regression gate and must pass without AWS credentials or network access.

## Rollout

1. Add provider-neutral contracts, AWS configuration, schema additions, and test fixtures.
2. Implement standard EC2 lifecycle and switch manual VM creation and destruction.
3. Implement GameNet networking, gateways, OPNsense instances, endpoints, and cleanup.
4. Port the OPNsense AMI build, validation, activation, retirement, and cleanup workflow.
5. Switch event readiness, preview, orchestration, UI/API terminology, deployment files, and documentation.
6. Run the disposable AWS canaries in an approved test account.
7. Remove Vultr Python and Ansible dependencies, environment variables, runtime routes, service credentials, code paths, and current-user-facing documentation.

This is a hard cutover for new infrastructure. Existing Vultr resources are outside the automation boundary and must be handled separately by operators.

## Acceptance Criteria

- All new standard and GameNet infrastructure is created and destroyed through AWS.
- The OPNsense and WireGuard architecture and learner experience are preserved.
- OPNsense AMIs can be built, validated twice, activated, used, retired, and cleaned up.
- Provisioning retries reconcile existing AWS resources without duplicates.
- Destruction leaves no tagged resources from the disposable acceptance run.
- Event start is blocked on unproven permissions, identity, AMIs, networking, or capacity.
- Normal Docker tests pass without AWS access; opt-in AWS acceptance tests pass in the approved account.
- Installed Python and Ansible dependencies contain no Vultr provider packages.
- Runtime code, active routes, UI, configuration, and current documentation contain no Vultr integration. Vultr references remain only in migrations or explicitly labelled historical material.

## Explicit Non-Goals

- Migrating live Vultr VMs, VPCs, snapshots, DNS records, or historical provider identifiers.
- Automatically destroying legacy Vultr resources.
- Replacing OPNsense or WireGuard with AWS-native firewalls, VPNs, peering, or transit services.
- Moving Semaphore guest configuration into cloud-init or an infrastructure-as-code stack.
- Supporting multiple cloud providers after the cutover.
