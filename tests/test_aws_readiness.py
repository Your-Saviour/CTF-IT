from api.services.aws.readiness import AwsReadinessService, ResourcePlan


class Sts:
    def get_caller_identity(self): return {"Account": "123456789012", "Arn": "arn:aws:iam::123:role/test"}


class Ec2:
    def _dry_run(self, operation):
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "DryRunOperation", "Message": "allowed"}}, operation)
    def run_instances(self, **kwargs): self._dry_run("RunInstances")
    def create_vpc(self, **kwargs): self._dry_run("CreateVpc")
    def allocate_address(self, **kwargs): self._dry_run("AllocateAddress")
    def describe_addresses(self): return {"Addresses": [{"AllocationId": "eipalloc-used"}] * 4}
    def describe_vpcs(self, **kwargs):
        if kwargs.get("VpcIds"):
            return {"Vpcs": [{"VpcId": "vpc-standard", "State": "available"}]}
        return {"Vpcs": []}
    def describe_security_groups(self, **kwargs):
        return {"SecurityGroups": [{"GroupId": value, "VpcId": "vpc-standard"}
                                    for value in kwargs["GroupIds"]]}
    def describe_network_interfaces(self): return {"NetworkInterfaces": []}
    def describe_instances(self, **kwargs): return {"Reservations": []}
    def describe_instance_types(self, **kwargs):
        sizes = {"t3.small": 2, "t3.medium": 2, "t3.large": 2}
        return {"InstanceTypes": [{
            "InstanceType": value, "VCpuInfo": {"DefaultVCpus": sizes[value]},
        } for value in kwargs["InstanceTypes"]]}
    def describe_instance_type_offerings(self, **kwargs):
        return {"InstanceTypeOfferings": [{"InstanceType": value} for value in kwargs["Filters"][0]["Values"]]}
    def describe_images(self, **kwargs): return {"Images": [{"ImageId": value, "State": "available"} for value in kwargs["ImageIds"]]}
    def describe_subnets(self, **kwargs):
        return {"Subnets": [{"SubnetId": "subnet-standard", "VpcId": "vpc-standard",
                             "AvailabilityZone": "ap-southeast-2a",
                             "AvailableIpAddressCount": 100}]}


class Quotas:
    values = {"L-0263D0A3": 5, "L-F678F1CE": 100, "L-DF5E4CA3": 5000,
              "L-1216C47A": 100}
    def get_service_quota(self, **kwargs):
        return {"Quota": {"Value": self.values[kwargs["QuotaCode"]]}}


def plan(**overrides):
    values = dict(vpcs=1, subnets=3, network_interfaces=4, elastic_ips=1,
                  instances_by_type={"t3.small": 1}, on_demand_vcpus=2)
    values.update(overrides)
    return ResourcePlan(**values)


def service(pricing=None):
    return AwsReadinessService(
        Sts(), Ec2(), Quotas(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
        ami_ids=("ami-ubuntu", "ami-freebsd"), vpc_id="vpc-standard",
        subnet_id="subnet-standard", security_group_ids=("sg-standard",), pricing=pricing,
    )


def test_readiness_blocks_insufficient_elastic_ips():
    report = service().check(plan(elastic_ips=2))
    assert report.ready is False
    assert report.checks["elastic_ips"].code == "quota_exceeded"


def test_price_lookup_failure_does_not_hide_capacity_success():
    class Pricing:
        def estimate(self, _plan): raise TimeoutError("pricing unavailable")
    report = service(Pricing()).check(plan(elastic_ips=1))
    assert report.ready is True
    assert report.estimated_hourly_cost is None


def test_gamenet_resource_plan_counts_dual_eni_firewalls_and_eips():
    from api.routes.admin import _aws_resource_plan
    infrastructure = {
        "vpn_gateway": {"default_plan": "t3.small"},
        "sites": [{
            "firewall": {"default_plan": "t3.medium"},
            "zones": [{"endpoints": [{"default_plan": "t3.small", "count": 2}]}],
        }],
    }
    result = _aws_resource_plan(infrastructure, 3)
    assert result.vpcs == 3 and result.subnets == 9
    assert result.elastic_ips == 6
    assert result.network_interfaces == 15
    assert result.instances_by_type == {"t3.small": 9, "t3.medium": 3}


def test_readiness_counts_existing_vpcs_enis_and_vcpus_before_passing_capacity():
    ec2 = Ec2()
    ec2.describe_vpcs = lambda **kwargs: ({
        "Vpcs": [{"VpcId": "vpc-standard", "State": "available"}],
    } if kwargs.get("VpcIds") else {
        "Vpcs": [{"VpcId": f"vpc-{i}"} for i in range(99)],
    })
    ec2.describe_network_interfaces = lambda: {
        "NetworkInterfaces": [{"NetworkInterfaceId": f"eni-{i}"} for i in range(4997)]
    }
    ec2.describe_instances = lambda **_: {"Reservations": [{"Instances": [{
        "InstanceId": "i-existing", "InstanceType": "t3.large",
    }]}]}
    report = AwsReadinessService(
        Sts(), ec2, Quotas(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
        ami_ids=("ami-ubuntu",), vpc_id="vpc-standard", subnet_id="subnet-standard",
        security_group_ids=("sg-standard",),
    ).check(plan(vpcs=2, network_interfaces=4, on_demand_vcpus=99))
    assert report.checks["vpcs"].code == "quota_exceeded"
    assert report.checks["network_interfaces"].code == "quota_exceeded"
    assert report.checks["on_demand_vcpus"].code == "quota_exceeded"


def test_readiness_uses_verified_service_namespaces_for_quotas():
    calls = []
    class RecordingQuotas(Quotas):
        def get_service_quota(self, **kwargs):
            calls.append((kwargs["ServiceCode"], kwargs["QuotaCode"]))
            return super().get_service_quota(**kwargs)
    service_ = AwsReadinessService(
        Sts(), Ec2(), RecordingQuotas(), region="ap-southeast-2",
        availability_zone="ap-southeast-2a", ami_ids=("ami-ubuntu",),
        vpc_id="vpc-standard", subnet_id="subnet-standard",
        security_group_ids=("sg-standard",),
    )
    service_.check(plan())
    assert ("ec2", "L-0263D0A3") in calls
    assert ("vpc", "L-F678F1CE") in calls
    assert ("vpc", "L-DF5E4CA3") in calls
    assert ("ec2", "L-1216C47A") in calls


def test_readiness_fails_when_standard_subnet_or_security_group_is_outside_vpc():
    ec2 = Ec2()
    ec2.describe_subnets = lambda **_: {"Subnets": [{
        "SubnetId": "subnet-standard", "VpcId": "vpc-other",
        "AvailabilityZone": "ap-southeast-2a", "AvailableIpAddressCount": 100,
    }]}
    report = AwsReadinessService(
        Sts(), ec2, Quotas(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
        ami_ids=("ami-ubuntu",), vpc_id="vpc-standard", subnet_id="subnet-standard",
        security_group_ids=("sg-standard",),
    ).check(plan())
    assert report.checks["standard_network"].passed is False
    assert report.checks["standard_network"].code == "network_mismatch"


def test_readiness_fails_closed_when_mutation_dry_run_is_unauthorized():
    from botocore.exceptions import ClientError
    ec2 = Ec2()
    ec2.create_vpc = lambda **_: (_ for _ in ()).throw(ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "denied ec2:CreateVpc"}},
        "CreateVpc",
    ))
    report = AwsReadinessService(
        Sts(), ec2, Quotas(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
        ami_ids=("ami-ubuntu",), vpc_id="vpc-standard", subnet_id="subnet-standard",
        security_group_ids=("sg-standard",),
    ).check(plan())
    assert report.checks["mutation_permissions"].passed is False
    assert report.checks["mutation_permissions"].code == "permission_denied"
    assert "CreateVpc" in report.checks["mutation_permissions"].detail
