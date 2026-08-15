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
    secondary_cidrs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityGroupSpec:
    vpc_id: str
    name: str
    description: str
    ingress: tuple[dict, ...]
    egress: tuple[dict, ...]
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

    def _ensure_secondary_cidr(self, vpc_id: str, cidr: str) -> None:
        response = self._call("describe_vpcs", VpcIds=[vpc_id])
        vpcs = response.get("Vpcs", [])
        associations = vpcs[0].get("CidrBlockAssociationSet", []) if vpcs else []
        if any(row.get("CidrBlock") == cidr and row.get("CidrBlockState", {}).get("State") != "failing"
               for row in associations):
            return
        self._call("associate_vpc_cidr_block", VpcId=vpc_id, CidrBlock=cidr)

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
        for cidr in spec.secondary_cidrs:
            self._ensure_secondary_cidr(vpc_id, cidr)
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

    @staticmethod
    def _permissions_equal(actual: list[dict], expected: tuple[dict, ...]) -> bool:
        def canonical(permission: dict) -> dict:
            return {
                key: permission[key]
                for key in ("IpProtocol", "FromPort", "ToPort", "IpRanges",
                            "Ipv6Ranges", "PrefixListIds", "UserIdGroupPairs")
                if key in permission and permission[key] not in (None, [])
            }
        return sorted((canonical(row) for row in actual), key=repr) == sorted(
            (canonical(row) for row in expected), key=repr,
        )

    def ensure_security_group(self, spec: SecurityGroupSpec) -> str:
        response = self._call("describe_security_groups", Filters=[
            {"Name": "vpc-id", "Values": [spec.vpc_id]},
            {"Name": "group-name", "Values": [spec.name]},
        ])
        if response.get("SecurityGroups"):
            group = response["SecurityGroups"][0]
            assert_owned(aws_tag_dict(group.get("Tags")), spec.tags)
        else:
            group_id = self._call(
                "create_security_group", VpcId=spec.vpc_id, GroupName=spec.name,
                Description=spec.description,
            )["GroupId"]
            self._call("create_tags", Resources=[group_id], Tags=aws_tag_list(spec.tags))
            group = {
                "GroupId": group_id, "IpPermissions": [],
                "IpPermissionsEgress": [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
            }
        group_id = group["GroupId"]
        current_ingress = group.get("IpPermissions", [])
        current_egress = group.get("IpPermissionsEgress", [])
        if not self._permissions_equal(current_ingress, spec.ingress):
            if current_ingress:
                self._call("revoke_security_group_ingress", GroupId=group_id,
                           IpPermissions=current_ingress)
            if spec.ingress:
                self._call("authorize_security_group_ingress", GroupId=group_id,
                           IpPermissions=list(spec.ingress))
        if not self._permissions_equal(current_egress, spec.egress):
            if current_egress:
                self._call("revoke_security_group_egress", GroupId=group_id,
                           IpPermissions=current_egress)
            if spec.egress:
                self._call("authorize_security_group_egress", GroupId=group_id,
                           IpPermissions=list(spec.egress))
        return group_id

    def delete_owned_vpc(self, vpc_id: str, expected_tags: Mapping[str, str]) -> None:
        response = self._call("describe_vpcs", VpcIds=[vpc_id])
        vpc = response["Vpcs"][0]
        assert_owned(aws_tag_dict(vpc.get("Tags")), expected_tags)
        self._call("delete_vpc", VpcId=vpc_id)
