"""Approved EC2 instance metadata and public On-Demand Linux pricing."""

import json


class AwsCatalogueService:
    def __init__(self, ec2, pricing, *, region: str, availability_zone: str):
        self.ec2 = ec2
        self.pricing = pricing
        self.region = region
        self.availability_zone = availability_zone

    def _hourly_price(self, instance_type: str) -> float:
        filters = [
            ("instanceType", instance_type),
            ("regionCode", self.region),
            ("operatingSystem", "Linux"),
            ("tenancy", "Shared"),
            ("preInstalledSw", "NA"),
            ("capacitystatus", "Used"),
        ]
        response = self.pricing.get_products(
            ServiceCode="AmazonEC2",
            Filters=[{"Type": "TERM_MATCH", "Field": field, "Value": value}
                     for field, value in filters],
            MaxResults=100,
        )
        prices = []
        for encoded in response.get("PriceList", []):
            product = json.loads(encoded) if isinstance(encoded, str) else encoded
            for term in product.get("terms", {}).get("OnDemand", {}).values():
                for dimension in term.get("priceDimensions", {}).values():
                    if dimension.get("unit") == "Hrs":
                        prices.append(float(dimension["pricePerUnit"]["USD"]))
        if not prices:
            raise RuntimeError(
                f"AWS Pricing returned no Linux On-Demand hourly price for {instance_type}"
            )
        return min(prices)

    def catalogue(self, approved_types: tuple[str, ...]) -> list[dict]:
        offerings = self.ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[
                {"Name": "instance-type", "Values": list(approved_types)},
                {"Name": "location", "Values": [self.availability_zone]},
            ],
        ).get("InstanceTypeOfferings", [])
        offered = {row["InstanceType"] for row in offerings}
        selected = tuple(kind for kind in approved_types if kind in offered)
        if not selected:
            return []
        metadata = self.ec2.describe_instance_types(InstanceTypes=list(selected)).get(
            "InstanceTypes", []
        )
        by_type = {row["InstanceType"]: row for row in metadata}
        return [{
            "instance_type": kind,
            "memory_mb": int(by_type[kind]["MemoryInfo"]["SizeInMiB"]),
            "vcpu": int(by_type[kind]["VCpuInfo"]["DefaultVCpus"]),
            "hourly_cost": self._hourly_price(kind),
            "regions": [self.region],
        } for kind in selected if kind in by_type]

    def estimate(self, plan) -> float:
        rows = self.catalogue(tuple(plan.instances_by_type))
        prices = {row["instance_type"]: row["hourly_cost"] for row in rows}
        missing = set(plan.instances_by_type) - set(prices)
        if missing:
            raise RuntimeError("AWS pricing unavailable for: " + ", ".join(sorted(missing)))
        return sum(prices[kind] * count for kind, count in plan.instances_by_type.items())
