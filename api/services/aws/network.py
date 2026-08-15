from dataclasses import dataclass
from typing import Mapping

from .compute import AwsComputeProvider
from .tags import assert_owned, aws_tag_dict, aws_tag_list
from .types import NetworkInterfaceResult, SiteNetworkResult


@dataclass(frozen=True)
class SiteNetworkSpec:
    region: str
    availability_zone: str
    vpc_cidr: str
    subnets: Mapping[str, str]
    tags: Mapping[str, str]


class AwsNetworkProvider:
    def __init__(self, ec2_client):
        self.ec2 = ec2_client
        self._compute = AwsComputeProvider(ec2_client)

    def _call(self, operation: str, **kwargs):
        return self._compute._call(operation, **kwargs)

    @staticmethod
    def _tag_spec(resource_type: str, tags: Mapping[str, str]) -> list[dict]:
        return [{"ResourceType": resource_type, "Tags": aws_tag_list(tags)}]

    def _ensure_vpc(self, spec: SiteNetworkSpec) -> str:
        response = self._call("describe_vpcs", Filters=[
            {"Name": "cidr-block", "Values": [spec.vpc_cidr]},
            {"Name": "tag:ManagedBy", "Values": [spec.tags["ManagedBy"]]},
            {"Name": "tag:SiteId", "Values": [spec.tags["SiteId"]]},
        ])
        if response.get("Vpcs"):
            vpc = response["Vpcs"][0]
            assert_owned(aws_tag_dict(vpc.get("Tags")), spec.tags)
            return vpc["VpcId"]
        vpc_id = self._call(
            "create_vpc", CidrBlock=spec.vpc_cidr,
            TagSpecifications=self._tag_spec("vpc", spec.tags),
        )["Vpc"]["VpcId"]
        self._call("modify_vpc_attribute", VpcId=vpc_id, EnableDnsSupport={"Value": True})
        self._call("modify_vpc_attribute", VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        return vpc_id

    def _ensure_igw(self, vpc_id: str, tags: Mapping[str, str]) -> str:
        response = self._call("describe_internet_gateways", Filters=[
            {"Name": "attachment.vpc-id", "Values": [vpc_id]},
        ])
        if response.get("InternetGateways"):
            gateway = response["InternetGateways"][0]
            assert_owned(aws_tag_dict(gateway.get("Tags")), tags)
            return gateway["InternetGatewayId"]
        gateway_id = self._call(
            "create_internet_gateway",
            TagSpecifications=self._tag_spec("internet-gateway", tags),
        )["InternetGateway"]["InternetGatewayId"]
        self._call("attach_internet_gateway", InternetGatewayId=gateway_id, VpcId=vpc_id)
        return gateway_id

    def _ensure_subnet(self, vpc_id: str, role: str, cidr: str, spec: SiteNetworkSpec) -> str:
        tags = {**spec.tags, "NetworkRole": role}
        response = self._call("describe_subnets", Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "cidr-block", "Values": [cidr]},
        ])
        if response.get("Subnets"):
            subnet = response["Subnets"][0]
            assert_owned(aws_tag_dict(subnet.get("Tags")), tags)
            return subnet["SubnetId"]
        return self._call(
            "create_subnet", VpcId=vpc_id, CidrBlock=cidr,
            AvailabilityZone=spec.availability_zone,
            TagSpecifications=self._tag_spec("subnet", tags),
        )["Subnet"]["SubnetId"]

    def _ensure_route_table(self, vpc_id: str, role: str, subnet_id: str,
                            tags: Mapping[str, str]) -> str:
        role_tags = {**tags, "NetworkRole": role}
        response = self._call("describe_route_tables", Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:NetworkRole", "Values": [role]},
        ])
        if response.get("RouteTables"):
            table = response["RouteTables"][0]
            assert_owned(aws_tag_dict(table.get("Tags")), role_tags)
            return table["RouteTableId"]
        table_id = self._call(
            "create_route_table", VpcId=vpc_id,
            TagSpecifications=self._tag_spec("route-table", role_tags),
        )["RouteTable"]["RouteTableId"]
        self._call("associate_route_table", RouteTableId=table_id, SubnetId=subnet_id)
        return table_id

    def ensure_site_network(self, spec: SiteNetworkSpec) -> SiteNetworkResult:
        vpc_id = self._ensure_vpc(spec)
        igw_id = self._ensure_igw(vpc_id, spec.tags)
        subnet_ids = {
            role: self._ensure_subnet(vpc_id, role, cidr, spec)
            for role, cidr in spec.subnets.items()
        }
        route_table_ids = {
            role: self._ensure_route_table(vpc_id, role, subnet_id, spec.tags)
            for role, subnet_id in subnet_ids.items()
        }
        self.ensure_route(route_table_ids["wan"], "0.0.0.0/0", gateway_id=igw_id)
        return SiteNetworkResult(
            vpc_id, spec.availability_zone, subnet_ids, route_table_ids, igw_id,
        )

    def ensure_route(self, route_table_id: str, destination: str, *,
                     eni_id: str | None = None, gateway_id: str | None = None) -> None:
        request = {"RouteTableId": route_table_id, "DestinationCidrBlock": destination}
        if eni_id:
            request["NetworkInterfaceId"] = eni_id
        elif gateway_id:
            request["GatewayId"] = gateway_id
        else:
            raise ValueError("A route requires an ENI or gateway target")
        self._call("create_route", **request)

    def create_eni(self, subnet_id: str, private_ip: str | None,
                   security_group_ids: list[str], tags: Mapping[str, str]) -> NetworkInterfaceResult:
        request = {
            "SubnetId": subnet_id, "Groups": security_group_ids,
            "TagSpecifications": self._tag_spec("network-interface", tags),
        }
        if private_ip:
            request["PrivateIpAddress"] = private_ip
        eni = self._call("create_network_interface", **request)["NetworkInterface"]
        return NetworkInterfaceResult(
            eni["NetworkInterfaceId"], subnet_id, eni.get("PrivateIpAddress"), eni.get("MacAddress")
        )

    def delete_owned_vpc(self, vpc_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_vpcs", VpcIds=[vpc_id])
        vpc = response["Vpcs"][0]
        assert_owned(aws_tag_dict(vpc.get("Tags")), expected_tags)
        self._call("delete_vpc", VpcId=vpc_id)
