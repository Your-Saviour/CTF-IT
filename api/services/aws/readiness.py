from dataclasses import dataclass
from typing import Mapping


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

    def __init__(self, sts, ec2, service_quotas, *, region: str,
                 availability_zone: str, ami_ids: tuple[str, ...], subnet_id: str,
                 pricing=None):
        self.sts = sts
        self.ec2 = ec2
        self.quotas = service_quotas
        self.region = region
        self.availability_zone = availability_zone
        self.ami_ids = ami_ids
        self.subnet_id = subnet_id
        self.pricing = pricing

    def _quota(self, code: str) -> int:
        response = self.quotas.get_service_quota(ServiceCode="ec2", QuotaCode=code)
        return int(response["Quota"]["Value"])

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
            in_use = len(self.ec2.describe_addresses().get("Addresses", []))
            quota = self._quota(self.EIP_QUOTA)
            checks["elastic_ips"] = (_passed(f"{quota - in_use} available") if in_use + plan.elastic_ips <= quota
                                      else _failed("quota_exceeded", f"need {plan.elastic_ips}, have {quota - in_use}"))
        except Exception as exc:
            checks["elastic_ips"] = _failed("quota_check_failed", str(exc))

        for name, code, required in (
            ("vpcs", self.VPC_QUOTA, plan.vpcs),
            ("on_demand_vcpus", self.VCPU_QUOTA, plan.on_demand_vcpus),
        ):
            try:
                quota = self._quota(code)
                checks[name] = (_passed(f"quota {quota}") if required <= quota
                                else _failed("quota_exceeded", f"need {required}, quota {quota}"))
            except Exception as exc:
                checks[name] = _failed("quota_check_failed", str(exc))

        checks["network_interfaces"] = _passed(f"requires {plan.network_interfaces}")
        estimated = None
        if self.pricing:
            try:
                estimated = self.pricing.estimate(plan)
            except Exception:
                estimated = None
        return ReadinessReport(account_id, self.region, self.availability_zone, checks, estimated)
