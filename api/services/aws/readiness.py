from dataclasses import dataclass
from typing import Mapping
from botocore.exceptions import ClientError

from .tags import aws_tag_list


@dataclass(frozen=True)
class ResourcePlan:
    vpcs: int
    subnets: int
    network_interfaces: int
    elastic_ips: int
    instances_by_type: Mapping[str, int]
    on_demand_vcpus: int


@dataclass(frozen=True)
class ReadinessCheck:
    passed: bool
    code: str
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    account_id: str | None
    region: str
    availability_zone: str
    checks: Mapping[str, ReadinessCheck]
    estimated_hourly_cost: float | None

    @property
    def ready(self) -> bool:
        return all(check.passed for check in self.checks.values())


def _passed(detail: str) -> ReadinessCheck:
    return ReadinessCheck(True, "ok", detail)


def _failed(code: str, detail: str) -> ReadinessCheck:
    return ReadinessCheck(False, code, detail)


class AwsReadinessService:
    EIP_QUOTA = "L-0263D0A3"
    VPC_QUOTA = "L-F678F1CE"
    VCPU_QUOTA = "L-1216C47A"
    ENI_QUOTA = "L-DF5E4CA3"

    def __init__(self, sts, ec2, service_quotas, *, region: str,
                 availability_zone: str, ami_ids: tuple[str, ...], vpc_id: str,
                 subnet_id: str, security_group_ids: tuple[str, ...],
                 pricing=None):
        self.sts = sts
        self.ec2 = ec2
        self.quotas = service_quotas
        self.region = region
        self.availability_zone = availability_zone
        self.ami_ids = ami_ids
        self.vpc_id = vpc_id
        self.subnet_id = subnet_id
        self.security_group_ids = security_group_ids
        self.pricing = pricing

    def _quota(self, service: str, code: str) -> int:
        response = self.quotas.get_service_quota(ServiceCode=service, QuotaCode=code)
        return int(response["Quota"]["Value"])

    def _all(self, operation: str, key: str, **kwargs) -> list[dict]:
        rows, token = [], None
        while True:
            request = dict(kwargs)
            if token:
                request["NextToken"] = token
            response = getattr(self.ec2, operation)(**request)
            rows.extend(response.get(key, []))
            token = response.get("NextToken")
            if not token:
                return rows

    def check(self, plan: ResourcePlan) -> ReadinessReport:
        checks: dict[str, ReadinessCheck] = {}
        account_id = None
        try:
            identity = self.sts.get_caller_identity()
            account_id = identity["Account"]
            checks["identity"] = _passed(identity.get("Arn", account_id))
        except Exception as exc:
            checks["identity"] = _failed("identity_failed", str(exc))

        try:
            tags = aws_tag_list({
                "Application": "ctf-it", "ManagedBy": "ctf-it",
                "Environment": "readiness-dry-run",
            })
            probes = (
                ("RunInstances", self.ec2.run_instances, {
                    "ImageId": self.ami_ids[0],
                    "InstanceType": next(iter(plan.instances_by_type), "t3.small"),
                    "MinCount": 1, "MaxCount": 1, "DryRun": True,
                    "NetworkInterfaces": [{
                        "DeviceIndex": 0, "SubnetId": self.subnet_id,
                        "Groups": list(self.security_group_ids),
                    }],
                    "TagSpecifications": [
                        {"ResourceType": resource, "Tags": tags}
                        for resource in ("instance", "network-interface", "volume")
                    ],
                }),
                ("CreateVpc", self.ec2.create_vpc, {
                    "CidrBlock": "10.255.240.0/28", "DryRun": True,
                    "TagSpecifications": [{"ResourceType": "vpc", "Tags": tags}],
                }),
                ("AllocateAddress", self.ec2.allocate_address, {
                    "Domain": "vpc", "DryRun": True,
                    "TagSpecifications": [{"ResourceType": "elastic-ip", "Tags": tags}],
                }),
            )
            for name, operation, request in probes:
                try:
                    operation(**request)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code")
                    if code == "DryRunOperation":
                        continue
                    message = exc.response.get("Error", {}).get("Message", str(exc))
                    raise PermissionError(f"{name}: {code}: {message}") from exc
                raise RuntimeError(f"{name}: AWS did not honor DryRun")
            checks["mutation_permissions"] = _passed(
                "EC2 launch, VPC creation, and Elastic IP allocation dry-runs authorized"
            )
        except Exception as exc:
            checks["mutation_permissions"] = _failed("permission_denied", str(exc))

        try:
            images = self.ec2.describe_images(ImageIds=list(self.ami_ids)).get("Images", [])
            available = {row["ImageId"] for row in images if row.get("State") == "available"}
            missing = set(self.ami_ids) - available
            checks["amis"] = _failed("ami_unavailable", ", ".join(sorted(missing))) if missing else _passed("approved AMIs available")
        except Exception as exc:
            checks["amis"] = _failed("ami_check_failed", str(exc))

        try:
            requested = tuple(plan.instances_by_type)
            response = self.ec2.describe_instance_type_offerings(
                LocationType="availability-zone",
                Filters=[{"Name": "instance-type", "Values": list(requested)},
                         {"Name": "location", "Values": [self.availability_zone]}],
            )
            offered = {row["InstanceType"] for row in response.get("InstanceTypeOfferings", [])}
            missing = set(requested) - offered
            checks["offerings"] = _failed("type_unavailable", ", ".join(sorted(missing))) if missing else _passed("instance types offered")
        except Exception as exc:
            checks["offerings"] = _failed("offering_check_failed", str(exc))

        try:
            subnets = self.ec2.describe_subnets(SubnetIds=[self.subnet_id]).get("Subnets", [])
            available = subnets[0]["AvailableIpAddressCount"] if subnets else 0
            required = sum(plan.instances_by_type.values())
            checks["subnet_addresses"] = (_passed(f"{available} available") if available >= required
                                           else _failed("capacity_exceeded", f"need {required}, have {available}"))
        except Exception as exc:
            checks["subnet_addresses"] = _failed("subnet_check_failed", str(exc))

        try:
            vpcs = self.ec2.describe_vpcs(VpcIds=[self.vpc_id]).get("Vpcs", [])
            subnets = self.ec2.describe_subnets(SubnetIds=[self.subnet_id]).get("Subnets", [])
            groups = self.ec2.describe_security_groups(
                GroupIds=list(self.security_group_ids),
            ).get("SecurityGroups", [])
            valid = (
                len(vpcs) == 1 and vpcs[0].get("State") == "available"
                and len(subnets) == 1 and subnets[0].get("VpcId") == self.vpc_id
                and {row.get("GroupId") for row in groups} == set(self.security_group_ids)
                and all(row.get("VpcId") == self.vpc_id for row in groups)
            )
            checks["standard_network"] = (
                _passed("configured VPC, subnet, and security groups are consistent")
                if valid else _failed(
                    "network_mismatch", "configured subnet or security group is outside the standard VPC",
                )
            )
        except Exception as exc:
            checks["standard_network"] = _failed("network_check_failed", str(exc))

        try:
            in_use = len(self._all("describe_addresses", "Addresses"))
            quota = self._quota("ec2", self.EIP_QUOTA)
            checks["elastic_ips"] = (_passed(f"{quota - in_use} available") if in_use + plan.elastic_ips <= quota
                                      else _failed("quota_exceeded", f"need {plan.elastic_ips}, have {quota - in_use}"))
        except Exception as exc:
            checks["elastic_ips"] = _failed("quota_check_failed", str(exc))

        try:
            used = len(self._all("describe_vpcs", "Vpcs"))
            quota = self._quota("vpc", self.VPC_QUOTA)
            available = quota - used
            checks["vpcs"] = (_passed(f"{available} available") if plan.vpcs <= available
                              else _failed("quota_exceeded", f"need {plan.vpcs}, have {available}"))
        except Exception as exc:
            checks["vpcs"] = _failed("quota_check_failed", str(exc))

        try:
            used = len(self._all("describe_network_interfaces", "NetworkInterfaces"))
            quota = self._quota("vpc", self.ENI_QUOTA)
            available = quota - used
            checks["network_interfaces"] = (
                _passed(f"{available} available") if plan.network_interfaces <= available
                else _failed("quota_exceeded", f"need {plan.network_interfaces}, have {available}")
            )
        except Exception as exc:
            checks["network_interfaces"] = _failed("quota_check_failed", str(exc))

        try:
            reservations = self._all(
                "describe_instances", "Reservations",
                Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}],
            )
            instances = [
                row for reservation in reservations for row in reservation.get("Instances", [])
                if row.get("InstanceLifecycle") != "spot"
            ]
            types = sorted({row["InstanceType"] for row in instances})
            vcpus = {}
            if types:
                response = self.ec2.describe_instance_types(InstanceTypes=types)
                vcpus = {
                    row["InstanceType"]: int(row["VCpuInfo"]["DefaultVCpus"])
                    for row in response.get("InstanceTypes", [])
                }
                if set(types) != set(vcpus):
                    raise RuntimeError("could not resolve vCPU usage for all running instance types")
            used = sum(vcpus[row["InstanceType"]] for row in instances)
            quota = self._quota("ec2", self.VCPU_QUOTA)
            available = quota - used
            checks["on_demand_vcpus"] = (
                _passed(f"{available} available") if plan.on_demand_vcpus <= available
                else _failed("quota_exceeded", f"need {plan.on_demand_vcpus}, have {available}")
            )
        except Exception as exc:
            checks["on_demand_vcpus"] = _failed("quota_check_failed", str(exc))
        estimated = None
        if self.pricing:
            try:
                estimated = self.pricing.estimate(plan)
            except Exception:
                estimated = None
        return ReadinessReport(account_id, self.region, self.availability_zone, checks, estimated)
