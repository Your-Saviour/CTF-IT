import json

from api.services.aws.catalogue import AwsCatalogueService
from api.services.aws.readiness import ResourcePlan


class Ec2:
    def describe_instance_types(self, **kwargs):
        values = {"t3.small": (2, 2048), "t3.medium": (2, 4096)}
        return {"InstanceTypes": [{
            "InstanceType": kind,
            "VCpuInfo": {"DefaultVCpus": values[kind][0]},
            "MemoryInfo": {"SizeInMiB": values[kind][1]},
        } for kind in kwargs["InstanceTypes"]]}

    def describe_instance_type_offerings(self, **kwargs):
        return {"InstanceTypeOfferings": [
            {"InstanceType": kind, "Location": "ap-southeast-2a"}
            for kind in kwargs["Filters"][0]["Values"]
        ]}


class Pricing:
    prices = {"t3.small": "0.0256000000", "t3.medium": "0.0512000000"}

    def get_products(self, **kwargs):
        kind = next(row["Value"] for row in kwargs["Filters"] if row["Field"] == "instanceType")
        product = {"terms": {"OnDemand": {"term": {"priceDimensions": {"dimension": {
            "unit": "Hrs", "pricePerUnit": {"USD": self.prices[kind]},
        }}}}}}
        return {"PriceList": [json.dumps(product)]}


def test_catalogue_reports_offered_capacity_and_linux_ondemand_price():
    catalogue = AwsCatalogueService(
        Ec2(), Pricing(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
    ).catalogue(("t3.small", "t3.medium"))
    assert catalogue == [
        {"instance_type": "t3.small", "memory_mb": 2048, "vcpu": 2,
         "hourly_cost": 0.0256, "regions": ["ap-southeast-2"]},
        {"instance_type": "t3.medium", "memory_mb": 4096, "vcpu": 2,
         "hourly_cost": 0.0512, "regions": ["ap-southeast-2"]},
    ]


def test_catalogue_estimates_hourly_plan_cost_by_instance_count():
    service = AwsCatalogueService(
        Ec2(), Pricing(), region="ap-southeast-2", availability_zone="ap-southeast-2a",
    )
    plan = ResourcePlan(1, 2, 3, 2, {"t3.small": 2, "t3.medium": 1}, 6)
    assert service.estimate(plan) == 0.1024
