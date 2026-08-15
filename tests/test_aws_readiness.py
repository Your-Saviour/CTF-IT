from api.services.aws.readiness import AwsReadinessService, ResourcePlan


class Sts:
    def get_caller_identity(self): return {"Account": "123456789012", "Arn": "arn:aws:iam::123:role/test"}


class Ec2:
    def describe_addresses(self): return {"Addresses": [{"AllocationId": "eipalloc-used"}] * 4}
    def describe_instance_type_offerings(self, **kwargs):
        return {"InstanceTypeOfferings": [{"InstanceType": value} for value in kwargs["Filters"][0]["Values"]]}
    def describe_images(self, **kwargs): return {"Images": [{"ImageId": value, "State": "available"} for value in kwargs["ImageIds"]]}
    def describe_subnets(self, **kwargs): return {"Subnets": [{"AvailableIpAddressCount": 100}]}


class Quotas:
    values = {"L-0263D0A3": 5, "L-F678F1CE": 100, "L-1216C47A": 100}
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
        ami_ids=("ami-ubuntu", "ami-freebsd"), subnet_id="subnet-standard", pricing=pricing,
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
