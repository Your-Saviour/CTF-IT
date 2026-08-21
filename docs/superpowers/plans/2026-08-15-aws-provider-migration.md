# AWS Provider Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every active Vultr integration with an idempotent AWS implementation while preserving standard VM provisioning and the complete OPNsense/WireGuard GameNet experience.

**Architecture:** Boto3-backed compute, network, and image providers own AWS mutations behind injected interfaces; the database remains the durable workflow record and Semaphore remains responsible for guest configuration. AWS resources are reconciled through persisted IDs, client tokens, and ownership tags, with GameNet sites represented by single-AZ VPCs containing public WAN and isolated zone subnets.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy/Alembic, Boto3/Botocore, Ansible Semaphore, Jinja2, pytest, Docker Compose, EC2/VPC/EBS/AMI/Service Quotas/Pricing APIs.

## Global Constraints

- The cutover applies only to newly provisioned resources; do not migrate or destroy existing Vultr infrastructure.
- Preserve OPNsense, WireGuard, isolated sites/zones, current participant profiles, module deployment, and acceptance checks.
- Use Boto3's standard AWS credential chain; production uses an IAM role and local development may use `AWS_PROFILE` or standard AWS environment credentials.
- Store no AWS secret keys in the service-credentials table.
- Use only administrator-approved Ubuntu and FreeBSD AMI IDs per region.
- Keep Semaphore responsible for guest configuration, never AWS resource lifecycle.
- Tag every supported resource with `Application=ctf-it`, `ManagedBy=ctf-it`, `Environment`, and applicable event/team/site/VM IDs.
- Refuse destructive operations when ownership tags do not match.
- Keep historical Vultr database columns readable, but expose no runtime operation for legacy Vultr resources.
- Normal Docker tests must pass without AWS credentials or network access.
- Disposable AWS acceptance tests are opt-in and must prove tagged cleanup.

---

## File Structure

Create these focused modules:

- `api/services/aws/__init__.py` — public AWS provider exports.
- `api/services/aws/config.py` — environment parsing and approved AMI/type catalogues.
- `api/services/aws/errors.py` — retryable, terminal, ownership, quota, and configuration errors.
- `api/services/aws/types.py` — provider result dataclasses and protocols used by orchestration.
- `api/services/aws/session.py` — regional Boto3 session/client creation and caller identity.
- `api/services/aws/tags.py` — canonical ownership tags and validation.
- `api/services/aws/compute.py` — EC2 instance, key pair, Elastic IP, ENI attachment, waiter, and termination operations.
- `api/services/aws/network.py` — VPC, subnet, gateway, route, ENI, security-group, and cleanup operations.
- `api/services/aws/images.py` — AMI and backing-snapshot lifecycle.
- `api/services/aws/readiness.py` — identity, configuration, offering, address, and quota preflight.
- `api/services/cloud_provisioning.py` — standard VM create/destroy workflows, separated from HTTP routes.
- `tests/test_aws_config.py`, `tests/test_aws_compute.py`, `tests/test_aws_network.py`, `tests/test_aws_images.py`, `tests/test_aws_readiness.py` — isolated provider tests.
- `tests/aws_acceptance/` — opt-in tagged AWS canaries and cleanup inventory.

Modify these existing areas:

- `api/models.py`, `migrations/versions/0010_aws_provider.py`, and `api/main.py` — neutral/AWS persistence and legacy compatibility.
- `builder/plan_sizing.py`, base YAML files, and validation tests — EC2 sizing and defaults.
- `api/routes/vm.py` — thin AWS-aware endpoints and event orchestration calls.
- `api/services/gamenet_provider.py` and `api/services/gamenet_provisioning.py` — AWS GameNet resource semantics while retaining guest/network configuration helpers.
- `api/services/opnsense_images.py` and `api/routes/admin.py` — AMI workflow and readiness/preview.
- `frontend/templates/admin.html`, `frontend/templates/vm_detail.html`, and `frontend/templates/topology.html` — AWS terminology and payloads.
- `requirements.txt`, playbooks, deployment files, environment examples, README, operator docs, and tests — final cutover and removal.

### Task 1: AWS Configuration, Types, Errors, Sessions, and Tags

**Files:**
- Create: `api/services/aws/__init__.py`
- Create: `api/services/aws/config.py`
- Create: `api/services/aws/errors.py`
- Create: `api/services/aws/types.py`
- Create: `api/services/aws/session.py`
- Create: `api/services/aws/tags.py`
- Create: `tests/test_aws_config.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `AwsConfig.from_env() -> AwsConfig`
- Produces: `AwsSessionFactory(config).client(service, region=None)` and `.caller_identity() -> AwsIdentity`
- Produces: `ownership_tags(environment, *, event_id=None, team_id=None, site_id=None, vm_id=None) -> dict[str, str]`
- Produces: `assert_owned(actual_tags, expected_tags) -> None`
- Produces: `InstanceResult`, `NetworkInterfaceResult`, `SiteNetworkResult`, and `ImageResult` immutable dataclasses.

- [ ] **Step 1: Add failing configuration and ownership tests**

```python
def test_config_requires_region_network_and_approved_amis(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_STANDARD_VPC_ID", "vpc-123")
    monkeypatch.setenv("AWS_STANDARD_SUBNET_ID", "subnet-123")
    monkeypatch.setenv("AWS_UBUNTU_AMIS", '{"ap-southeast-2":"ami-ubuntu"}')
    monkeypatch.setenv("AWS_FREEBSD_AMIS", '{"ap-southeast-2":"ami-freebsd"}')
    config = AwsConfig.from_env()
    assert config.ubuntu_ami("ap-southeast-2") == "ami-ubuntu"
    assert config.freebsd_ami("ap-southeast-2") == "ami-freebsd"

def test_assert_owned_rejects_mismatched_vm():
    with pytest.raises(AwsOwnershipError):
        assert_owned(
            {"Application": "ctf-it", "ManagedBy": "ctf-it", "VmId": "8"},
            {"Application": "ctf-it", "ManagedBy": "ctf-it", "VmId": "7"},
        )
```

- [ ] **Step 2: Run the focused test and confirm missing-module failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_config.py`

Expected: FAIL because `api.services.aws` does not exist.

- [ ] **Step 3: Implement configuration, result types, errors, session creation, and tags**

```python
@dataclass(frozen=True)
class AwsConfig:
    default_region: str
    environment: str
    standard_vpc_id: str
    standard_subnet_id: str
    ubuntu_amis: Mapping[str, str]
    freebsd_amis: Mapping[str, str]
    instance_types: tuple[str, ...]
    profile: str | None = None

class AwsSessionFactory:
    def __init__(self, config: AwsConfig):
        self.config = config
        self._session = boto3.Session(
            profile_name=config.profile,
            region_name=config.default_region,
        )

    def client(self, service: str, region: str | None = None):
        return self._session.client(service, region_name=region or self.config.default_region)

def ownership_tags(environment: str, **ids: int | None) -> dict[str, str]:
    tags = {"Application": "ctf-it", "ManagedBy": "ctf-it", "Environment": environment}
    names = {"event_id": "EventId", "team_id": "TeamId", "site_id": "SiteId", "vm_id": "VmId"}
    tags.update({names[key]: str(value) for key, value in ids.items() if value is not None})
    return tags
```

Add compatible pinned `boto3` and `botocore` versions to `requirements.txt`; never pass explicit secrets into `boto3.Session`.

- [ ] **Step 4: Run focused tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_config.py`

Expected: PASS.

- [ ] **Step 5: Commit the AWS foundation**

```bash
git add requirements.txt api/services/aws tests/test_aws_config.py
git commit -m "feat: add AWS provider foundation"
```

### Task 2: Neutral Cloud Schema and EC2 Instance Sizing

**Files:**
- Create: `migrations/versions/0010_aws_provider.py`
- Create: `tests/test_aws_schema.py`
- Modify: `api/models.py`
- Modify: `api/main.py`
- Modify: `builder/plan_sizing.py`
- Modify: `tests/test_provisioning.py`
- Modify: `bases/ubuntu_24_server/ubuntu_24_server.yaml`
- Modify: `bases/opnsense/opnsense.yaml`

**Interfaces:**
- Produces VM fields: `cloud_instance_id`, `instance_type`, `cloud_region`, `availability_zone`, `primary_eni_id`, `wan_eni_id`, `lan_eni_id`, `subnet_id`, `security_group_ids_json`.
- Produces Site fields: `availability_zone`, `public_subnet_id`, `infrastructure_subnet_id`, `internet_gateway_id`, `route_table_ids_json`.
- Produces OpnsenseImage fields: `ami_id`, `backing_snapshot_ids_json`, `region`, `availability_zone`, `builder_subnet_id`, `validation_subnet_id`.
- Changes `plan_for_vm(...) -> str` to consume catalogue entries `{instance_type, memory_mb, vcpu, hourly_cost, regions}`.

- [ ] **Step 1: Write failing migration and sizing tests**

```python
def test_vm_model_exposes_neutral_aws_fields():
    columns = VM.__table__.columns
    assert {"cloud_instance_id", "instance_type", "cloud_region", "availability_zone"} <= set(columns.keys())

def test_sizing_selects_cheapest_offered_ec2_type():
    catalogue = [
        {"instance_type": "t3.small", "memory_mb": 2048, "vcpu": 2, "hourly_cost": 0.02, "regions": ["ap-southeast-2"]},
        {"instance_type": "t3.medium", "memory_mb": 4096, "vcpu": 2, "hourly_cost": 0.04, "regions": ["ap-southeast-2"]},
    ]
    assert plan_for_vm(base, modules, None, catalogue, region="ap-southeast-2") == "t3.small"
```

- [ ] **Step 2: Run schema and provisioning tests to verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_schema.py tests/test_provisioning.py -k 'sizing or neutral_aws'`

Expected: FAIL on missing fields and old Vultr catalogue keys.

- [ ] **Step 3: Add the additive Alembic migration and ORM fields**

```python
revision = "0010_aws_provider"
down_revision = "0009_existing_feature_columns"

VM_COLUMNS = {
    "cloud_instance_id": sa.String(64), "instance_type": sa.String(64),
    "cloud_region": sa.String(32), "availability_zone": sa.String(32),
    "primary_eni_id": sa.String(64), "wan_eni_id": sa.String(64),
    "lan_eni_id": sa.String(64), "subnet_id": sa.String(64),
    "security_group_ids_json": sa.Text(),
}
```

Use idempotent column inspection like migration `0009`; add the same fields to the legacy `api/main.py` compatibility block. Do not copy `vultr_id`, plan, region, snapshot, ISO, or VPC identifiers into AWS fields.

- [ ] **Step 4: Convert base defaults and sizing vocabulary**

```yaml
# bases/ubuntu_24_server/ubuntu_24_server.yaml
os: "Ubuntu 24.04 LTS"
default_plan: t3.small
```

```python
candidates = [
    row for row in available_plans
    if region in row["regions"]
    and row["memory_mb"] >= required_ram
    and row["vcpu"] >= required_vcpu
]
return min(candidates, key=lambda row: row["hourly_cost"])["instance_type"]
```

Use `t3.medium` as the OPNsense default floor and preserve the existing fallback/warning behavior with EC2 names.

- [ ] **Step 5: Run migration and sizing tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_schema.py tests/test_provisioning.py`

Expected: PASS.

- [ ] **Step 6: Commit schema and sizing**

```bash
git add migrations/versions/0010_aws_provider.py api/models.py api/main.py builder/plan_sizing.py bases tests/test_aws_schema.py tests/test_provisioning.py
git commit -m "feat: add AWS cloud schema and sizing"
```

### Task 3: EC2 Compute Provider

**Files:**
- Create: `api/services/aws/compute.py`
- Create: `tests/test_aws_compute.py`
- Modify: `api/services/aws/__init__.py`

**Interfaces:**
- Consumes: `AwsConfig`, `AwsSessionFactory`, ownership tags, provider dataclasses/errors.
- Produces: `ensure_key_pair(name, public_key, tags) -> str`
- Produces: `launch_instance(spec: InstanceSpec) -> InstanceResult`
- Produces: `instance(instance_id) -> InstanceResult`, `wait_running(instance_id)`, `wait_stopped(instance_id)`.
- Produces: `allocate_eip(tags)`, `associate_eip(allocation_id, eni_id)`, `set_source_dest_check(instance_id, enabled)`.
- Produces: `terminate_owned(instance_id, expected_tags)`.

- [ ] **Step 1: Write failing Stubber tests for idempotency and ownership**

```python
def test_launch_uses_client_token_tags_imdsv2_and_explicit_eni(ec2_client):
    stubber = Stubber(ec2_client)
    stubber.add_response("describe_instances", {"Reservations": []}, expected_describe_by_token)
    stubber.add_response("run_instances", run_response("i-123"), {
        "ImageId": "ami-ubuntu", "InstanceType": "t3.small",
        "ClientToken": "ctf-it-vm-7", "MinCount": 1, "MaxCount": 1,
        "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        "NetworkInterfaces": [expected_primary_eni],
        "TagSpecifications": expected_instance_and_volume_tags,
    })
    result = provider(ec2_client).launch_instance(spec(vm_id=7))
    assert result.instance_id == "i-123"

def test_terminate_refuses_foreign_instance(ec2_client):
    add_describe_instance(ec2_client, instance_id="i-foreign", tags={"ManagedBy": "someone-else"})
    with pytest.raises(AwsOwnershipError):
        provider(ec2_client).terminate_owned("i-foreign", owned_vm_tags(7))
```

- [ ] **Step 2: Run provider tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_compute.py`

Expected: FAIL because `AwsComputeProvider` is missing.

- [ ] **Step 3: Implement compute operations and error translation**

```python
class AwsComputeProvider:
    def launch_instance(self, spec: InstanceSpec) -> InstanceResult:
        existing = self._find_by_client_token(spec.client_token)
        if existing:
            self._assert_instance_owned(existing, spec.tags)
            return self._result(existing)
        response = self.ec2.run_instances(
            ImageId=spec.ami_id,
            InstanceType=spec.instance_type,
            ClientToken=spec.client_token,
            MinCount=1,
            MaxCount=1,
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            NetworkInterfaces=spec.network_interfaces,
            TagSpecifications=tag_specifications(spec.tags, include_volumes=True),
            UserData=spec.user_data or "",
        )
        return self._result(response["Instances"][0])
```

Translate `ClientError` codes `RequestLimitExceeded`, `Throttling`, `InternalError`, and eventual not-found states into retryable errors. Treat unauthorized, invalid parameter/AMI/type, quota, and ownership errors as terminal typed errors. Use AWS waiters with explicit delay and max attempts.

- [ ] **Step 4: Run compute tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_compute.py`

Expected: PASS, including retry translation and EIP release coverage.

- [ ] **Step 5: Commit compute provider**

```bash
git add api/services/aws tests/test_aws_compute.py
git commit -m "feat: add EC2 compute provider"
```

### Task 4: VPC Network Provider and Dependency-Ordered Cleanup

**Files:**
- Create: `api/services/aws/network.py`
- Create: `tests/test_aws_network.py`
- Modify: `api/services/aws/__init__.py`

**Interfaces:**
- Consumes: AWS foundation and result types.
- Produces: `ensure_site_network(SiteNetworkSpec) -> SiteNetworkResult`.
- Produces: `ensure_security_group(SecurityGroupSpec) -> str` with exact-rule reconciliation.
- Produces: `create_eni(subnet_id, private_ip, security_group_ids, tags) -> NetworkInterfaceResult`.
- Produces: `ensure_route(route_table_id, destination, eni_id)`.
- Produces: `cleanup_site_network(recorded_ids, expected_tags) -> CleanupResult`.

- [ ] **Step 1: Write failing network topology and cleanup tests**

```python
def test_site_network_creates_public_infrastructure_and_zone_subnets_in_one_az():
    result = fake_backed_provider.ensure_site_network(site_spec(
        cidr="10.40.0.0/16", az="ap-southeast-2a",
        subnets={"wan": "10.40.0.0/24", "infra": "10.40.1.0/24", "blue": "10.40.10.0/24"},
    ))
    assert result.availability_zone == "ap-southeast-2a"
    assert set(result.subnet_ids) == {"wan", "infra", "blue"}
    assert result.internet_gateway_id.startswith("igw-")

def test_cleanup_refuses_vpc_without_expected_site_tag():
    with pytest.raises(AwsOwnershipError):
        provider.cleanup_site_network(ids(vpc_id="vpc-foreign"), site_tags(site_id=12))
```

- [ ] **Step 2: Verify tests fail**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_network.py`

Expected: FAIL because the network provider is absent.

- [ ] **Step 3: Implement deterministic site network reconciliation**

```python
@dataclass(frozen=True)
class SiteNetworkSpec:
    region: str
    availability_zone: str
    vpc_cidr: str
    subnets: Mapping[str, str]
    tags: Mapping[str, str]

def ensure_site_network(self, spec: SiteNetworkSpec) -> SiteNetworkResult:
    vpc_id = self._ensure_vpc(spec.vpc_cidr, spec.tags)
    igw_id = self._ensure_internet_gateway(vpc_id, spec.tags)
    subnet_ids = {
        key: self._ensure_subnet(vpc_id, cidr, spec.availability_zone, {**spec.tags, "NetworkRole": key})
        for key, cidr in spec.subnets.items()
    }
    route_table_ids = self._ensure_route_tables(vpc_id, subnet_ids, igw_id, spec.tags)
    return SiteNetworkResult(vpc_id, spec.availability_zone, subnet_ids, route_table_ids, igw_id)
```

Canonicalize ingress and egress rules before comparison. Delete in this order: instances handled by caller, EIPs, secondary ENIs, non-main routes, route-table associations, non-main route tables, security groups, subnets, internet gateway, VPC. Treat dependency violations as retryable until the bounded cleanup deadline.

- [ ] **Step 4: Run network tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_network.py`

Expected: PASS.

- [ ] **Step 5: Commit network provider**

```bash
git add api/services/aws tests/test_aws_network.py
git commit -m "feat: add AWS network provider"
```

### Task 5: Standard EC2 VM Lifecycle and API Cutover

**Files:**
- Create: `api/services/cloud_provisioning.py`
- Create: `tests/test_cloud_provisioning.py`
- Modify: `api/routes/vm.py`
- Modify: `tests/test_provisioning.py`

**Interfaces:**
- Consumes: `AwsComputeProvider`, `AwsConfig`, Semaphore client, DNS configuration, VM model.
- Produces: `create_cloud_vm(vm_id, providers=None) -> None`.
- Produces: `destroy_cloud_vm(vm_id, providers=None) -> None`.
- Produces endpoints: `GET /admin/api/aws/instance-types`, `GET /admin/api/aws/amis`, `POST /admin/api/vms/create-cloud`, `POST /admin/api/vms/{id}/retry-cloud`, `POST /admin/api/vms/{id}/destroy-cloud`.

- [ ] **Step 1: Write failing workflow tests with injected fakes**

```python
def test_create_persists_instance_before_guest_configuration(db, fake_compute, fake_semaphore):
    fake_semaphore.on_start = lambda: assert_vm(db, cloud_instance_id="i-123", status="provisioning")
    create_cloud_vm(vm.id, providers=providers(fake_compute, fake_semaphore))
    db.refresh(vm)
    assert vm.cloud_instance_id == "i-123"
    assert vm.public_ip == "198.51.100.20"
    assert vm.status == "ready"

def test_retry_reconciles_existing_instance_without_second_launch(db, fake_compute):
    vm.cloud_instance_id = "i-123"
    create_cloud_vm(vm.id, providers=providers(fake_compute))
    assert fake_compute.launch_calls == 0
    assert fake_compute.describe_calls == ["i-123"]
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_cloud_provisioning.py tests/test_provisioning.py -k 'cloud or retry'`

Expected: FAIL on missing workflow and endpoints.

- [ ] **Step 3: Implement the durable standard VM workflow**

```python
def create_cloud_vm(vm_id: int, providers: CloudProviders | None = None) -> None:
    with workflow_session() as db:
        vm = require_vm(db, vm_id)
        set_phase(db, vm, "creating_instance")
        result = providers.compute.reconcile_or_launch(instance_spec_for(vm, providers.config))
        vm.cloud_instance_id = result.instance_id
        vm.primary_eni_id = result.primary_eni_id
        vm.private_ip = result.private_ip
        allocation = providers.compute.reconcile_vm_eip(vm, result.primary_eni_id)
        vm.public_ip = allocation.public_ip
        db.commit()
        providers.compute.wait_running(result.instance_id)
        wait_for_ssh(vm)
        reconcile_dns(vm)
        run_semaphore_guest_configuration(db, vm)
        vm.status, vm.provision_step, vm.provision_error = "ready", "complete", None
        db.commit()
```

Move shared hostname normalization, DNS handling, task staging, and error redaction out of Vultr-named helpers. Destruction verifies ownership, terminates the EC2 instance, waits for termination, releases tracked EIP/ENIs, deletes DNS, then removes the VM row.

- [ ] **Step 4: Replace standard VM routes and payloads**

Return instance types from the approved catalogue filtered by the selected region/AZ and return only configured approved AMIs. Remove direct cloud HTTP calls and the Vultr Semaphore lifecycle project; continue using the event/guest configuration Semaphore project.

- [ ] **Step 5: Run standard provisioning tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_cloud_provisioning.py tests/test_provisioning.py tests/test_event_lifecycle.py`

Expected: PASS.

- [ ] **Step 6: Commit standard VM lifecycle**

```bash
git add api/services/cloud_provisioning.py api/routes/vm.py tests/test_cloud_provisioning.py tests/test_provisioning.py
git commit -m "feat: provision standard VMs on EC2"
```

### Task 6: Port GameNet Provider Semantics to AWS

**Files:**
- Modify: `api/services/gamenet_provider.py`
- Modify: `tests/test_gamenet.py`
- Modify: `playbooks/configure-vpc-interface.yml`
- Modify: `templates/vpc-netplan.yaml.j2`

**Interfaces:**
- Replaces `VultrGameNetProvider` with `AwsGameNetProvider(compute, network, config)`.
- Produces: `create_vpc(site) -> SiteNetworkResult`, `create_instance(...) -> InstanceResult`, `attach_network(...) -> NetworkInterfaceResult`, `apply_security_policy(...)`, and owned cleanup calls.
- Retains guest helpers for SSH, OPNsense rendering/configuration, WireGuard, endpoint network finalization, and connectivity checks.

- [ ] **Step 1: Translate provider tests to AWS resource behavior**

```python
def test_opnsense_launch_uses_wan_and_lan_enis_in_site_az(provider):
    result = provider.create_firewall(site, vm, ami_id="ami-opnsense")
    assert result.availability_zone == site.availability_zone
    assert provider.compute.last_spec.network_interfaces[0].subnet_id == site.public_subnet_id
    assert provider.compute.last_spec.network_interfaces[1].subnet_id == site.infrastructure_subnet_id
    assert provider.compute.source_dest_checks == [(result.instance_id, False)]

def test_private_endpoint_has_no_public_ip_and_routes_via_opnsense(provider):
    result = provider.create_endpoint(vm, site, zone)
    assert result.public_ip is None
    assert provider.network.routes[-1].target_eni_id == firewall.lan_eni_id
```

- [ ] **Step 2: Run GameNet tests and confirm failures**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_gamenet.py`

Expected: FAIL while tests still encounter Vultr methods/fields.

- [ ] **Step 3: Implement the AWS adapter and preserve guest helpers**

```python
class AwsGameNetProvider:
    def create_firewall(self, site: Site, vm: VM, *, ami_id: str) -> InstanceResult:
        wan = self.network.create_eni(site.public_subnet_id, None, [site.wan_security_group_id], vm_tags(vm))
        lan = self.network.create_eni(site.infrastructure_subnet_id, vm.private_ip, [site.lan_security_group_id], vm_tags(vm))
        result = self.compute.launch_instance(firewall_spec(vm, site, ami_id, wan, lan))
        self.compute.set_source_dest_check(result.instance_id, False)
        allocation = self.compute.allocate_eip(vm_tags(vm))
        self.compute.associate_eip(allocation.allocation_id, wan.eni_id)
        return replace(result, public_ip=allocation.public_ip, wan_eni_id=wan.eni_id, lan_eni_id=lan.eni_id)
```

Replace Vultr MAC/VPC attachment assumptions with EC2 ENI ID, MAC address, device index, and private-IP metadata. Update netplan discovery to select the interface by recorded MAC rather than assuming `ens7`; retain platform MTU defaults unless an explicit AWS requirement is configured.

- [ ] **Step 4: Run GameNet provider and SSH tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_gamenet.py tests/test_ssh_connection.py`

Expected: PASS.

- [ ] **Step 5: Commit the GameNet provider adapter**

```bash
git add api/services/gamenet_provider.py playbooks/configure-vpc-interface.yml templates/vpc-netplan.yaml.j2 tests/test_gamenet.py
git commit -m "feat: map GameNet resources to AWS"
```

### Task 7: Port Event GameNet Orchestration and Cleanup

**Files:**
- Modify: `api/services/gamenet_provisioning.py`
- Modify: `api/routes/vm.py`
- Modify: `api/routes/admin.py`
- Modify: `tests/test_gamenet.py`
- Modify: `tests/test_event_lifecycle.py`

**Interfaces:**
- Consumes: `AwsGameNetProvider`, persisted AWS fields, active OPNsense AMI.
- Produces unchanged top-level workflow: `provision_event_gamenets(event_id) -> None`.
- Produces retryable phase reconciliation and `cleanup_event_gamenets(event_id) -> CleanupResult`.

- [ ] **Step 1: Write failing phase, retry, and open-gate tests**

```python
def test_event_stays_closed_until_aws_acceptance_checks_pass(db, provider):
    provider.acceptance_error = AwsTerminalError("private endpoint exposed")
    provision_event_gamenets(event.id, provider_factory=lambda: provider)
    db.refresh(event)
    assert event.status == "provision_failed"
    assert event.open is False

def test_retry_reuses_recorded_vpc_and_firewall(db, provider):
    site.vpc_id, firewall.cloud_instance_id = "vpc-123", "i-fw"
    provision_event_gamenets(event.id, provider_factory=lambda: provider)
    assert provider.created_vpcs == []
    assert provider.described_instances == ["i-fw"]
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_gamenet.py tests/test_event_lifecycle.py -k 'aws or retry or acceptance'`

Expected: FAIL because orchestration still instantiates the Vultr provider and uses Vultr fields.

- [ ] **Step 3: Rewire orchestration phases to AWS resources**

```python
def _provider() -> AwsGameNetProvider:
    config = AwsConfig.from_env()
    sessions = AwsSessionFactory(config)
    return AwsGameNetProvider(
        AwsComputeProvider(sessions, config),
        AwsNetworkProvider(sessions, config),
        config,
    )

def _create_provider_vpc(site):
    result = _provider().create_vpc(site)
    site.vpc_id = result.vpc_id
    site.availability_zone = result.availability_zone
    site.public_subnet_id = result.subnet_ids["wan"]
    site.infrastructure_subnet_id = result.subnet_ids["infra"]
    site.route_table_ids_json = json.dumps(result.route_table_ids, sort_keys=True)
```

Keep phases durable: placeholders/addresses, networks, gateways, firewalls, tunnels, endpoints, modules, control-plane connection, final security policy, and acceptance. Each phase reconciles recorded resources. Event stop/delete invokes owned dependency-ordered cleanup and reports incomplete resources without forgetting them.

- [ ] **Step 4: Remove legacy VM-route GameNet implementations**

Delete `_create_team_vpc`, `_run_firewall_create`, and `_run_configure_vpc_interface` from `api/routes/vm.py` after all callers use `gamenet_provisioning.py`. Keep HTTP routes thin and remove direct provider requests.

- [ ] **Step 5: Run GameNet and event lifecycle suites**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_gamenet.py tests/test_event_lifecycle.py tests/test_event_dashboard.py`

Expected: PASS.

- [ ] **Step 6: Commit GameNet orchestration**

```bash
git add api/services/gamenet_provisioning.py api/routes/vm.py api/routes/admin.py tests/test_gamenet.py tests/test_event_lifecycle.py
git commit -m "feat: orchestrate GameNet on AWS"
```

### Task 8: AWS OPNsense AMI Build, Validation, Activation, and Retirement

**Files:**
- Create: `api/services/aws/images.py`
- Create: `tests/test_aws_images.py`
- Modify: `api/services/opnsense_images.py`
- Modify: `api/routes/admin.py`
- Modify: `tests/test_opnsense_images.py`

**Interfaces:**
- Produces `AwsImageProvider.create_image(instance_id, name, tags) -> ImageResult`.
- Produces `wait_available(ami_id)`, `launch_validation_instance(...)`, and `retire_owned(ami_id, snapshot_ids, expected_tags)`.
- Changes `new_image(..., provider_factory=...)` and `run_image_build(..., provider_factory=...)` to provider-neutral injection.
- Preserves `active_image`, phase persistence, evidence recording, resume behavior, and safe cleanup.

- [ ] **Step 1: Write failing AMI provider tests**

```python
def test_create_image_records_all_backing_snapshots(ec2_client):
    add_create_image(ec2_client, instance_id="i-builder", image_id="ami-new")
    add_describe_image(ec2_client, "ami-new", snapshots=["snap-root", "snap-data"])
    result = provider(ec2_client).create_image("i-builder", "ctf-opnsense-26-7", image_tags)
    assert result.ami_id == "ami-new"
    assert result.snapshot_ids == ("snap-root", "snap-data")

def test_retire_deregisters_before_owned_snapshot_delete(ec2_client):
    provider(ec2_client).retire_owned("ami-old", ["snap-root"], image_tags)
    assert call_names(ec2_client) == ["describe_images", "deregister_image", "describe_snapshots", "delete_snapshot"]
```

- [ ] **Step 2: Translate workflow fixture names and expected phases**

Replace `VultrImageClient`, `vultr_factory`, ISO/snapshot clone vocabulary, and attach-VPC fixtures with `AwsImageProvider`, `provider_factory`, AMI validation launches, ENIs, subnets, and EBS snapshot IDs. Retain tests proving two builder boots, clean halt, two independent clones, resume from every durable phase, activation gating, and cleanup failure safety.

- [ ] **Step 3: Run image tests and confirm failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_images.py tests/test_opnsense_images.py`

Expected: FAIL until the AMI provider and workflow conversion exist.

- [ ] **Step 4: Implement AMI lifecycle and workflow mapping**

```python
def create_image(self, instance_id: str, name: str, tags: Mapping[str, str]) -> ImageResult:
    self.compute.wait_stopped(instance_id)
    image_id = self.ec2.create_image(InstanceId=instance_id, Name=name, NoReboot=False)["ImageId"]
    self.wait_available(image_id)
    image = self.ec2.describe_images(ImageIds=[image_id])["Images"][0]
    snapshot_ids = tuple(
        mapping["Ebs"]["SnapshotId"] for mapping in image["BlockDeviceMappings"] if "Ebs" in mapping
    )
    self.ec2.create_tags(Resources=[image_id, *snapshot_ids], Tags=aws_tag_list(tags))
    return ImageResult(image_id, snapshot_ids, image["State"])
```

The workflow creates a temporary builder VPC with public and isolated validation subnets, launches the approved regional FreeBSD AMI, runs the existing bootstrap and guest validation, stops the builder, creates the AMI, then launches public and isolated validation instances from it. Store AMI and snapshot IDs before validation. Activation requires both fingerprints and connectivity evidence. Retirement refuses referenced AMIs and verifies ownership on the AMI and every snapshot.

- [ ] **Step 5: Run image workflow tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_images.py tests/test_opnsense_images.py`

Expected: PASS.

- [ ] **Step 6: Commit image workflow**

```bash
git add api/services/aws/images.py api/services/opnsense_images.py api/routes/admin.py tests/test_aws_images.py tests/test_opnsense_images.py
git commit -m "feat: build and certify OPNsense AMIs"
```

### Task 9: AWS Readiness, Capacity, Pricing, and Event Preview

**Files:**
- Create: `api/services/aws/readiness.py`
- Create: `tests/test_aws_readiness.py`
- Modify: `api/routes/admin.py`
- Modify: `builder/infrastructure_validation.py`
- Modify: `tests/test_event_plan_template.py`
- Modify: `tests/test_event_lifecycle.py`
- Modify: `tests/test_gamenet.py`

**Interfaces:**
- Produces `AwsReadinessService.check(plan: ResourcePlan) -> ReadinessReport`.
- `ReadinessReport` contains identity, region/AZ, AMI, instance-type, permissions, VPC, subnet address, Elastic IP, ENI, On-Demand vCPU, and estimated-cost results.
- Event preview returns `aws_resources`, `aws_capacity`, and `estimated_hourly_cost`; price unavailability is non-blocking, all capacity/identity failures are blocking.

- [ ] **Step 1: Write failing capacity and error-classification tests**

```python
def test_readiness_blocks_insufficient_elastic_ips(fake_aws):
    fake_aws.eip_quota = 5
    fake_aws.eips_in_use = 4
    report = service(fake_aws).check(plan(elastic_ips=2))
    assert report.ready is False
    assert report.checks["elastic_ips"].code == "quota_exceeded"

def test_price_lookup_failure_does_not_hide_capacity_success(fake_aws):
    fake_aws.pricing_error = TimeoutError()
    report = service(fake_aws).check(plan(elastic_ips=1))
    assert report.ready is True
    assert report.estimated_hourly_cost is None
```

- [ ] **Step 2: Run readiness tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_aws_readiness.py tests/test_event_lifecycle.py -k 'readiness or quota or preview'`

Expected: FAIL because readiness still performs Vultr VPC/plan calls.

- [ ] **Step 3: Implement plan calculation and AWS checks**

```python
@dataclass(frozen=True)
class ResourcePlan:
    vpcs: int
    subnets: int
    network_interfaces: int
    elastic_ips: int
    instances_by_type: Mapping[str, int]
    on_demand_vcpus: int

def check(self, plan: ResourcePlan) -> ReadinessReport:
    identity = self.sts.get_caller_identity()
    checks = {
        "identity": passed(identity["Account"]),
        "amis": self._check_amis(),
        "offerings": self._check_instance_type_offerings(plan),
        "subnet_addresses": self._check_available_addresses(plan),
        "elastic_ips": self._check_quota("L-0263D0A3", plan.elastic_ips),
        "vpcs": self._check_quota("L-F678F1CE", plan.vpcs),
        "network_interfaces": self._check_eni_capacity(plan.network_interfaces),
        "on_demand_vcpus": self._check_on_demand_vcpu(plan.on_demand_vcpus),
    }
    return ReadinessReport(identity["Account"], checks, self._optional_price(plan))
```

Use EC2 `DescribeInstanceTypeOfferings`, `DescribeImages`, subnet `AvailableIpAddressCount`, Service Quotas, and STS. Perform a documented dry-run or read-level permission checks before mutations; report the exact missing operation when AWS returns `UnauthorizedOperation`.

- [ ] **Step 4: Replace admin readiness and preview payloads**

Remove `_live_vpc_counts` and Vultr plan retrieval. Ensure `start_event` executes AWS preflight before setting `status=provisioning` or starting background workers.

- [ ] **Step 5: Run admin/event tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_aws_readiness.py tests/test_event_lifecycle.py tests/test_event_plan_template.py tests/test_gamenet.py`

Expected: PASS.

- [ ] **Step 6: Commit readiness and preview**

```bash
git add api/services/aws/readiness.py api/routes/admin.py builder/infrastructure_validation.py tests/test_aws_readiness.py tests/test_event_lifecycle.py tests/test_event_plan_template.py tests/test_gamenet.py
git commit -m "feat: validate AWS event capacity"
```

### Task 10: Admin UI, VM Detail, Credentials, and API Contract Cutover

**Files:**
- Modify: `frontend/templates/admin.html`
- Modify: `frontend/templates/vm_detail.html`
- Modify: `frontend/templates/topology.html`
- Modify: `frontend/static/admin.js`
- Modify: `api/main.py`
- Modify: `api/schemas.py`
- Modify: `tests/test_event_plan_template.py`
- Modify: `tests/test_provisioning.py`
- Modify: `tests/test_secrets.py`

**Interfaces:**
- Consumes AWS endpoints from Tasks 5 and 9.
- UI uses `cloud_instance_id`, `instance_type`, `cloud_region`, `availability_zone`, AMI selection, and AWS readiness details.
- Service catalogue displays non-secret AWS identity/region status and never stores access credentials.

- [ ] **Step 1: Write failing contract and template tests**

```python
def test_admin_template_uses_aws_routes_and_no_vultr_actions():
    html = Path("frontend/templates/admin.html").read_text()
    assert "/admin/api/aws/instance-types" in html
    assert "Create on AWS" in html
    assert "Vultr" not in html

def test_vm_detail_destroy_action_is_provider_neutral():
    html = Path("frontend/templates/vm_detail.html").read_text()
    assert "/destroy-cloud" in html
    assert "Destroy EC2 instance" in html
    assert "destroy-vultr" not in html
```

- [ ] **Step 2: Run UI/contract tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_event_plan_template.py tests/test_provisioning.py tests/test_secrets.py`

Expected: FAIL while templates and seeded credentials still name Vultr.

- [ ] **Step 3: Convert forms, JavaScript, status text, and details**

```javascript
async function fetchAwsOptions() {
  const [amis, types] = await Promise.all([
    fetch('/admin/api/aws/amis').then(requireJson),
    fetch('/admin/api/aws/instance-types').then(requireJson),
  ]);
  cachedAwsAmis = amis.amis;
  cachedAwsInstanceTypes = types.instance_types;
}

function destroyCloudVM() {
  if (!confirm('Destroy this EC2 instance and remove its managed DNS record?')) return;
  return fetch(`/admin/api/vms/${VM_ID}/destroy-cloud`, {method: 'POST'});
}
```

Render region, AZ, instance type, instance ID, public/private address, and AWS readiness. Rename Vultr JavaScript variables, element IDs, comments, status labels, and errors. Do not display or accept AWS secret fields.

- [ ] **Step 4: Replace seeded Vultr credential entry**

Seed an informational `AWS Provider` catalogue record containing region/account-status text and an empty encrypted placeholder only if the current schema requires a password. Prefer changing the service catalogue so non-secret integrations need no password, with a migration/test if required.

- [ ] **Step 5: Run UI, route, and credential tests**

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_event_plan_template.py tests/test_provisioning.py tests/test_secrets.py tests/test_event_dashboard.py`

Expected: PASS.

- [ ] **Step 6: Commit UI and API cutover**

```bash
git add frontend api/main.py api/schemas.py tests/test_event_plan_template.py tests/test_provisioning.py tests/test_secrets.py
git commit -m "feat: switch cloud UI and API to AWS"
```

### Task 11: Deployment, Playbook, Documentation, and Dependency Cutover

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `deploy/docker-compose.yml`
- Create: `deploy/aws/iam-policy.json`
- Modify: `quickstart.sh`
- Modify: `playbooks/collections/requirements.yml`
- Delete: `playbooks/create-vm.yml`
- Delete: `playbooks/create-firewall.yml`
- Delete: `playbooks/destroy-vm.yml`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `TEST_PLAN.md`
- Modify: `DOCKER_TEST_PLAN.md`
- Modify: `SERVICE_CREDENTIALS_TEST_PLAN.md`
- Modify: `deploy/testing_plan.md`
- Modify: `deploy/VM_ONBOARDING_PROBLEM.md`
- Move: `docs/KNOWN_ISSUES_VULTR_PRIVATE_BOOT.md` to `docs/historical/KNOWN_ISSUES_VULTR_PRIVATE_BOOT.md`
- Modify: `tests/test_deploy_compose.py`
- Modify: `tests/test_quickstart.py`

**Interfaces:**
- Deployment passes standard AWS configuration without secret defaults.
- Ansible collections contain only guest-configuration dependencies.
- Current documentation explains IAM roles, local profiles, AMI approval, quotas, canaries, cleanup, and the hard-cutover treatment of legacy Vultr resources.

- [ ] **Step 1: Write failing repository and deployment tests**

```python
def test_runtime_tree_has_no_vultr_integration():
    forbidden = runtime_vultr_matches(exclude=["migrations", "docs/historical", "docs/superpowers"])
    assert forbidden == []

def test_compose_passes_aws_configuration_without_secret_values():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    env = compose["services"]["api"]["environment"]
    assert "AWS_DEFAULT_REGION" in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env

def test_iam_policy_contains_only_documented_aws_services():
    policy = json.loads(Path("deploy/aws/iam-policy.json").read_text())
    actions = {action for statement in policy["Statement"] for action in statement["Action"]}
    assert all(action.split(":", 1)[0] in {"ec2", "sts", "servicequotas", "pricing"} for action in actions)
    assert "iam:*" not in actions and "ec2:*" not in actions
```

- [ ] **Step 2: Run deployment tests and verify failure**

Run: `docker compose --profile test run --rm tests pytest -q tests/test_deploy_compose.py tests/test_quickstart.py`

Expected: FAIL on Vultr environment and playbook references.

- [ ] **Step 3: Remove cloud lifecycle playbooks and Vultr collection**

```yaml
---
collections:
  - name: community.general
```

Confirm no Semaphore template references the deleted lifecycle playbooks. Keep guest configuration playbooks such as bootstrap and interface configuration.

- [ ] **Step 4: Update environment and deployment configuration**

```dotenv
AWS_DEFAULT_REGION=ap-southeast-2
AWS_STANDARD_VPC_ID=vpc-replace-me
AWS_STANDARD_SUBNET_ID=subnet-replace-me
AWS_UBUNTU_AMIS={"ap-southeast-2":"ami-replace-me"}
AWS_FREEBSD_AMIS={"ap-southeast-2":"ami-replace-me"}
AWS_INSTANCE_TYPES=t3.small,t3.medium,t3.large
AWS_ENVIRONMENT=production
```

Document `AWS_PROFILE` only for local use. Do not add access-key placeholders to Compose. Update quickstart validation to require region, VPC/subnet, AMI mappings, control-plane CIDR, and environment name when cloud provisioning is enabled.

Create `deploy/aws/iam-policy.json` with the exact EC2 mutation and describe actions used by the providers, `sts:GetCallerIdentity`, `servicequotas:GetServiceQuota`, and the pricing lookup action. Scope taggable EC2 mutations with `aws:ResourceTag/ManagedBy=ctf-it` where AWS supports resource-level conditions; keep unavoidable create and describe permissions explicit rather than wildcarding an entire service. Document that operators attach this policy to the production workload role.

- [ ] **Step 5: Rewrite active operator documentation**

Describe IAM-role deployment, a least-privilege policy file, approved AMI mapping, ordinary VM networking, GameNet single-AZ site topology, VPC/EIP/vCPU quota planning, OPNsense AMI lifecycle, retry and cleanup behavior, and opt-in acceptance tests. Move the Vultr private-boot issue into `docs/historical/` with a banner stating it applies only to pre-cutover infrastructure.

- [ ] **Step 6: Run repository scans and deployment tests**

Run: `rg -n -i 'vultr' api builder bases frontend playbooks requirements.txt .env.example docker-compose.yml deploy quickstart.sh README.md CLAUDE.md TEST_PLAN.md DOCKER_TEST_PLAN.md SERVICE_CREDENTIALS_TEST_PLAN.md`

Expected: no matches.

Run: `docker compose --profile test run --rm --build tests pytest -q tests/test_deploy_compose.py tests/test_quickstart.py`

Expected: PASS.

- [ ] **Step 7: Commit deployment and documentation cutover**

```bash
git add -A .env.example docker-compose.yml deploy quickstart.sh playbooks README.md CLAUDE.md TEST_PLAN.md DOCKER_TEST_PLAN.md SERVICE_CREDENTIALS_TEST_PLAN.md docs tests/test_deploy_compose.py tests/test_quickstart.py
git commit -m "docs: complete AWS deployment cutover"
```

### Task 12: Disposable AWS Acceptance Suite and Final Regression

**Files:**
- Create: `tests/aws_acceptance/README.md`
- Create: `tests/aws_acceptance/conftest.py`
- Create: `tests/aws_acceptance/test_standard_vm.py`
- Create: `tests/aws_acceptance/test_gamenet_site.py`
- Create: `tests/aws_acceptance/test_opnsense_ami.py`
- Create: `scripts/aws_acceptance_cleanup.py`
- Modify: `TEST_PLAN.md`

**Interfaces:**
- Acceptance suite requires `RUN_AWS_ACCEPTANCE=1`, an approved test account ID, a unique run ID, and explicit region/network/AMI configuration.
- Every created resource includes `AcceptanceRunId=<run id>`.
- Cleanup script inventories and removes only matching owned resources, then exits nonzero if any remain.

- [ ] **Step 1: Write acceptance safety tests that run without AWS**

```python
def test_acceptance_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_AWS_ACCEPTANCE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_acceptance_context()

def test_cleanup_filter_requires_run_id_and_expected_account():
    with pytest.raises(ValueError):
        CleanupContext(run_id="", expected_account_id="123456789012")
```

- [ ] **Step 2: Run safety tests locally**

Run: `docker compose --profile test run --rm tests pytest -q tests/aws_acceptance -k 'opt_in or cleanup_filter'`

Expected: PASS without making network calls.

- [ ] **Step 3: Implement opt-in standard VM canary**

```python
def test_standard_vm_create_configure_destroy(aws_context):
    vm = aws_context.create_standard_vm()
    assert aws_context.wait_for_ssh(vm.public_ip)
    assert aws_context.run_guest_smoke(vm) == "ctf-it-ready"
    aws_context.destroy_standard_vm(vm)
    assert aws_context.inventory() == []
```

- [ ] **Step 4: Implement opt-in GameNet and OPNsense canaries**

```python
def test_gamenet_site_is_private_and_routes_through_opnsense(aws_context):
    site = aws_context.create_gamenet_site()
    assert site.endpoint.public_ip is None
    assert aws_context.wireguard_reaches(site.endpoint.private_ip)
    assert aws_context.internet_egress_uses_firewall(site)
    assert aws_context.forbidden_public_ports_are_closed(site)
```

The AMI test builds a candidate from the approved FreeBSD AMI, validates both launch modes, activates it, launches one firewall, retires the candidate after references are removed, and verifies AMI plus backing snapshots are gone.

- [ ] **Step 5: Run the complete offline regression suite**

Run: `docker compose --profile test run --rm --build tests`

Expected: PASS; AWS acceptance cases skip because `RUN_AWS_ACCEPTANCE` is unset.

- [ ] **Step 6: Run static and repository checks**

Run: `docker compose config >/dev/null && git diff --check && rg -n -i 'vultr' api builder bases frontend playbooks requirements.txt .env.example docker-compose.yml deploy quickstart.sh README.md CLAUDE.md TEST_PLAN.md DOCKER_TEST_PLAN.md SERVICE_CREDENTIALS_TEST_PLAN.md`

Expected: Compose config and diff checks pass; the Vultr scan returns no matches.

- [ ] **Step 7: Run canaries in the approved AWS test account**

Run: `RUN_AWS_ACCEPTANCE=1 AWS_ACCEPTANCE_ACCOUNT_ID=<approved-account-id> AWS_ACCEPTANCE_RUN_ID=<unique-run-id> docker compose --profile test run --rm tests pytest -q tests/aws_acceptance`

Expected: PASS, followed by an empty inventory for `AcceptanceRunId=<unique-run-id>`. Never run this command until the operator supplies the approved account and explicit opt-in values.

- [ ] **Step 8: Commit acceptance coverage**

```bash
git add tests/aws_acceptance scripts/aws_acceptance_cleanup.py TEST_PLAN.md
git commit -m "test: add disposable AWS acceptance coverage"
```

## Final Completion Gate

- [ ] Run `docker compose --profile test run --rm --build tests` and record the passing summary.
- [ ] Run `docker compose config >/dev/null` and `git diff --check`.
- [ ] Run the active-tree Vultr scan from Task 12 and confirm zero matches.
- [ ] Confirm `git status --short` contains only intended changes.
- [ ] Review the implementation against every acceptance criterion in `docs/superpowers/specs/2026-08-15-aws-provider-migration-design.md`.
- [ ] If an approved AWS test account is available, run all acceptance canaries and confirm the final tagged inventory is empty; otherwise report this external verification as outstanding without claiming it passed.
